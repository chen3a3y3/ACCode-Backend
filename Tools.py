import boto3
import tempfile
import os
import shutil
import configparser
import json
from subprocess import check_output, CalledProcessError, call, Popen, PIPE
from git import Repo
from datetime import datetime
import time

config = configparser.ConfigParser()
config.read('config.ini')
ANDROID_TOOLS_HOME = config['DEFAULT']['AndroidToolsHome']
GRADLE_VERSION = config['DEFAULT']['GradleVersion']
ANDROID_TARGET_VERSION = config['DEFAULT']['AndroidTargetVersion']
TEMP_APP_SRC = config['DEFAULT']['TmpAppSrc']
CODEBUILD_SERVICE_ROLE = config['AWS']['CodeBuildServiceRole']
S3_BUCKET = config['AWS']['S3Bucket']


def _datetime_from_utc_to_local(utc_ts):
    now_timestamp = time.time()
    offset = datetime.fromtimestamp(now_timestamp) - datetime.utcfromtimestamp(now_timestamp)
    return int(utc_ts - 18000000)


def create_code_build_project(appName, description='', *args):
    codecommit_url = 'https://git-codecommit.us-east-1.amazonaws.com/v1/repos/' + appName
    client = boto3.client('codebuild')
    response = client.create_project(
        name=appName,
        description=description,
        source={
            'type': 'CODECOMMIT',
            'location': codecommit_url,
            'buildspec': 'buildspec.yml'
        },
        artifacts={
            'type': 'S3',
            'location': 'arn:aws:s3:::' + S3_BUCKET
        },
        environment={
            'type': 'LINUX_CONTAINER',
            'image': 'aws/codebuild/android-java-8:24.4.1',
            'computeType': 'BUILD_GENERAL1_SMALL',
            'environmentVariables': [],
            'privilegedMode': False
        },
        serviceRole=CODEBUILD_SERVICE_ROLE
    )
    print(response)


def build_project(projectName):
    client = boto3.client('codebuild')
    response = client.start_build(projectName=projectName)
    print(response)
    return response['build']['id']


def get_app_pid(appName):
    cmd = ['adb', 'shell', 'ps', '|', 'grep', '-i', appName, '|', 'cut', '-c10-15']
    ret = _exec_cmd(cmd)
    if ret[0] == 0:
        return int(ret[1].decode('utf-8').strip())
    else:
        return -1


def get_buildlogs(projectId, startTime=0):
    projectName, logStreamName = projectId.split(':')
    logGroupName = '/aws/codebuild/' + projectName
    client = boto3.client('logs')
    logEvents = client.get_log_events(
        logGroupName=logGroupName, logStreamName=logStreamName, startTime=startTime)
    print(logEvents)
    return logEvents


def get_applogs(appName, startTime=0):
    startTime = _datetime_from_utc_to_local(startTime)
    pid = get_app_pid(appName)
    if pid <= 0:
        return {'lastAppLogTimestamp': startTime, 'appLog': ''}
    ps = Popen(('adb', 'logcat', '-t', datetime.fromtimestamp(startTime / 1000).strftime("%m-%d %H:%M:%S.000")), stdout=PIPE)
    grep = Popen(['grep', '-i', str(pid)], stdin=ps.stdout, stdout=PIPE)
    print(datetime.fromtimestamp(startTime / 1000).strftime("%m-%d %H:%M:%S.000"))
    ret = grep.communicate()[0]
    ts = round(datetime.timestamp(datetime.now()) * 1000)
    return {'lastAppLogTimestamp': ts, 'appLog': ret.decode('utf-8')}


def get_apk_name(projectName):
    return projectName + '-app.apk'


def get_apk_s3_path(projectName):
    return projectName + '/app-debug.apk'


def install_apk(projectName, apkPath):
    # download apk from s3
    s3 = boto3.resource('s3')
    localApkName = get_apk_name(projectName)
    s3.meta.client.download_file(
        S3_BUCKET, apkPath, localApkName)
    # remove old one
    aapt = Popen('aapt dump badging {}'.format(localApkName).split(), stdout=PIPE)
    grep = Popen('grep package'.split(), stdin=aapt.stdout, stdout=PIPE)
    cut = Popen(['cut', "-d'", '-f2'], stdin=grep.stdout, stdout=PIPE)
    pkgName = cut.communicate()[0].decode('utf-8').strip()
    _exec_cmd('adb uninstall {}'.format(pkgName).split())
    installCmd = ['adb', 'install', '-r', localApkName]
    result = _exec_cmd(installCmd)
    print(result)
    os.remove(localApkName)
    return result


def _generate_build_gradle(packageName, applicationName, projectPath):
    templateScript = '''apply plugin: 'com.android.application'

android {{
    compileSdkVersion 26
    buildToolsVersion "26.0.1"
    defaultConfig {{
        applicationId "{}.{}"
        minSdkVersion 21
        targetSdkVersion 26
        versionCode 1
        versionName "1.0"
        testInstrumentationRunner "android.support.test.runner.AndroidJUnitRunner"
    }}
    buildTypes {{
        release {{
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android.txt'), 'proguard-rules.pro'
        }}
    }}
}}

dependencies {{
    implementation fileTree(dir: 'libs', include: ['*.jar'])
    implementation 'com.android.support:appcompat-v7:26.1.0'
    implementation 'com.android.support.constraint:constraint-layout:1.0.2'
    testImplementation 'junit:junit:4.12'
    androidTestImplementation('com.android.support.test.espresso:espresso-core:3.0.1', {{
        exclude group: 'com.android.support', module: 'support-annotations'
    }})
}}
'''
    gradlePath = os.path.join(projectPath, 'app', 'build.gradle')
    finalScript = templateScript.format(packageName, applicationName)
    with open(gradlePath, 'w') as f:
        f.write(finalScript)


def _generate_project_src(packageName, applicationName, projectPath):
    androidBinPath = os.path.join(ANDROID_TOOLS_HOME, 'android')
    createProjectScript = [
        androidBinPath,
        'create',
        'project',
        '--gradle',
        '--gradle-version',
        '3.0.0',
        '--activity',
        'Main',
        '--package',
        packageName + '.' + applicationName,
        '--target',
        'android-26',
        '--path',
        './tmp'
    ]
    result = _exec_cmd(createProjectScript)


def _generate_project_meta(projectPath):
    # copy template project to project path
    shutil.copytree('./AndroidTemplateApplication', projectPath)

    # copy generated src file to project path
    shutil.copytree(os.path.join(TEMP_APP_SRC, 'src'),
                    os.path.join(projectPath, 'app', 'src'))

    # delete generated src
    shutil.rmtree(TEMP_APP_SRC)


def generate_project(packageName, applicationName, projectPath):
    _generate_project_src(packageName, applicationName, projectPath)
    _generate_project_meta(projectPath)
    _generate_build_gradle(packageName, applicationName, projectPath)


def _exec_cmd(cmd):
    t = tempfile.TemporaryFile()
    try:
        output = check_output(cmd, stderr=t)
    except CalledProcessError as e:
        t.seek(0)
        result = e.returncode, t.read()
    else:
        result = 0, output
    return result


def create_remote_repo(appName, description=''):
    client = boto3.client('codecommit')
    response = client.create_repository(
        repositoryName=appName,
        repositoryDescription=description
    )
    print(response)


def delete_remote_repo(appName):
    client = boto3.client('codecommit')
    response = client.delete_repository(
        repositpryName=appName
    )
    print(response)


def local_repo(appName, projectPath):
    remote_path = 'https://git-codecommit.us-east-1.amazonaws.com/v1/repos/' + appName
    repo = Repo.init(projectPath)
    git_add_file(projectPath)
    repo.index.commit("init")
    os.chdir(projectPath)
    command = ['git', 'push', remote_path, '--all']
    result = _exec_cmd(command)
    os.chdir(os.path.dirname(__file__))


def git_add_file(projectPath):
    os.chdir(projectPath)
    command = ['git', 'add', '.']
    result = _exec_cmd(command)
    os.chdir(os.path.dirname(__file__))


def git_commit(projectPath, description='save changes'):
    repo = Repo(projectPath)
    repo.index.commit(description)


# TODO: fix this!!!
def git_push(appName, projectPath):
    remote_path = 'https://git-codecommit.us-east-1.amazonaws.com/v1/repos/' + appName
    os.chdir(projectPath)
    ret = _exec_cmd(['git', 'push', remote_path, '--all'])
    os.chdir(os.path.dirname(__file__))


def modify_file(filePath, content, projectPath):
    try:
        with open(filePath, 'w') as f:
            f.write(content)
    except:
        return {'result': -1, 'errmsg': 'no such file'}
    git_add_file(projectPath)
    return {'result': 0, 'errmsg': ''}


def rename_file(filePath, newFilePath, projectPath):
    if not os.path.exists(filePath):
        return {'result': -1, 'errmsg': 'no such file or directort'}
    os.rename(filePath, newFilePath)
    # git_add_file(projectPath)
    return {'result': 0, 'errmsg': ''}


def create_file(filePath, projectPath, isFolder=False):
    if os.path.exists(filePath):
        return {'result': -1, 'errmsg': 'There has the same name file or directory'}
    if not isFolder:
        with open(filePath, 'w') as fp:
            fp.write('\r\n')
    else:
        os.makedirs(filePath)

    git_add_file(projectPath)
    return {'result': 0, 'errmsg': ''}


def delete_file(filePath, projectPath):
    if not os.path.exists(filePath):
        return {'result': -1, 'errmsg': 'no such file'}
    if os.path.isfile(filePath):
        os.remove(filePath)
    else:
        shutil.rmtree(filePath)

    git_add_file(projectPath)
    return {'result': 0, 'errmsg': ''}


def init_project(packageName, appName, projectPath, description=''):
    generate_project(packageName, appName, projectPath)
    create_remote_repo(appName, description)
    local_repo(appName, projectPath)
    create_code_build_project(appName)


def main():
    # projectName = 'android-test-2'
    # build_project('android-build-sdk-base')
    buildId = 'helloapp:1ead4d59-3811-4847-a560-6f1eaea040d0'
    packageName = 'com.rexz'
    appName = 'testapp'
    projectPath = os.path.join('./', appName)
    # print(build_project(appName))
    # init_project(packageName, appName, projectPath)
    # build_project(appName)
    # get_buildlogs(buildId, 1510031877000)
    # install_apk('', '')
    # generate_project(packageName, appName, os.path.join('./', appName))
    # print(get_app_pid(appName))
    print(get_applogs(appName, 1511661132057))
    # install_apk(appName, appName + '/app-debug.apk')


if __name__ == '__main__':
    main()
