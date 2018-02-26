from flask import Flask, request, Response
import Explorer
import Tools
import json
import os
from flask_cors import CORS
import redis
import datetime
import configparser

app = Flask(__name__)
CORS(app)
red = redis.StrictRedis(host='flask-sse.mm0dvv.0001.use1.cache.amazonaws.com')

config = configparser.ConfigParser()
config.read('config.ini')
PROJECT_STORAGE = config['DEFAULT']['ProjectStorage']


def get_project_dir(appName):
    return os.path.join(PROJECT_STORAGE, appName)


def event_stream(project):
    pubsub = red.pubsub()
    pubsub.subscribe(project)
    for msg in pubsub.listen():
        if type(msg['data']) is not int:
            yield 'data: %s\n\n' % msg['data'].decode()


@app.route("/", methods=['GET'])
def index():
    print('project name: ', request.args.get('name'))
    print('package name: ', request.args.get('package'))
    print('description: ', request.args.get('description'))
    return "hello world"


@app.route("/explorer/getdir", methods=['GET'])
def get_dir():
    appName = request.args.get('project')
    baseDir = get_project_dir(appName)
    ret = Explorer.list_directory_recursive(baseDir)
    return json.dumps(ret)


@app.route("/explorer/project", methods=['GET'])
def get_file():
    appName = request.args.get('project')
    path = request.args.get('path')
    baseDir = get_project_dir(appName)
    filePath = baseDir + path
    return Explorer.read_file_content(filePath)


@app.route("/explorer/createFolder", methods=['POST'])
def create_folder():
    data = json.loads(request.data)
    appName = data['appName']
    currentFolder = data['currentFolder'][1:]
    isFolder = data['isFolder']
    name = data['name']
    baseDir = get_project_dir(appName)
    finalPath = os.path.join(baseDir, currentFolder, name)
    ret = Tools.create_file(finalPath, baseDir, True)
    return json.dumps(ret)


@app.route("/explorer/createFile", methods=['POST'])
def create_file():
    data = json.loads(request.data)
    appName = data['appName']
    currentFolder = data['currentFolder'][1:]
    isFolder = data['isFolder']
    name = data['name']
    baseDir = get_project_dir(appName)
    finalPath = os.path.join(baseDir, currentFolder, name)
    ret = Tools.create_file(finalPath, baseDir, False)
    return json.dumps(ret)


@app.route("/explorer/deleteFolder", methods=['POST'])
def delete_folder():
    data = json.loads(request.data)
    appName = data['appName']
    currentFolder = data['currentFolder'][1:]
    isFolder = data['isFolder']
    baseDir = get_project_dir(appName)
    finalPath = os.path.join(baseDir, currentFolder)
    ret = Tools.delete_file(finalPath, baseDir)
    return json.dumps(ret)


@app.route("/explorer/deleteFile", methods=['POST'])
def delete_file():
    data = json.loads(request.data)
    appName = data['appName']
    currentFolder = data['currentFilePath'][1:]
    isFolder = data['isFolder']
    baseDir = get_project_dir(appName)
    finalPath = os.path.join(baseDir, currentFolder)
    ret = Tools.delete_file(finalPath, baseDir)
    return json.dumps(ret)


@app.route("/tools/build", methods=['GET'])
def build_project():
    projectName = request.args.get('project')
    Tools.git_commit(get_project_dir(projectName))
    Tools.git_push(projectName, get_project_dir(projectName))
    buildId = Tools.build_project(projectName)
    # buildId = projectName + ':' + str(round(datetime.datetime.utcnow().timestamp() * 1000))
    return buildId


@app.route('/tools/buildlog', methods=['GET'])
def get_buildlog():
    buildId = request.args.get('buildId')
    startTime = int(request.args.get('startTime'))
    logEvents = Tools.get_buildlogs(buildId, startTime)
    # return json.dumps({'time': round(datetime.datetime.utcnow().timestamp() * 1000)})
    return json.dumps(logEvents)


@app.route('/tools/applog', methods=['GET'])
def get_applog():
    appName = request.args.get('appName')
    startTime = int(request.args.get('startTime'))
    appLogs = Tools.get_applogs(appName, startTime)
    print(appLogs, flush=True)
    return json.dumps(appLogs)


@app.route('/tools/createProject', methods=['POST'])
def create_project():
    data = json.loads(request.data)
    appName = data['Project Name']
    packageName = data['Package Name']
    description = data['Description']
    projectDir = get_project_dir(appName)
    Tools.init_project(packageName, appName, projectDir, description)
    return json.dumps(data)


@app.route('/tools/save', methods=['POST'])
def save_project():
    data = json.loads(request.data)
    appName = data['appName']
    path = data['path'][1:]
    code = data['code']
    projectDir = get_project_dir(appName)
    filePath = os.path.join(projectDir, path)
    ret = Tools.modify_file(filePath, code, projectDir)
    return json.dumps(ret)


@app.route('/subscribeServer')
def subscribe_server():
    project = request.args.get('project')
    return Response(event_stream(project), mimetype='text/event-stream')


@app.route('/push', methods=['POST'])
def push_to_client():
    print('asd')
    data = request.get_json()
    if data['action'] == 'build-finished':
        Tools.install_apk(data['project'], data['data'])
    red.publish(data['project'], json.dumps(data))
    return json.dumps(data)


@app.route('/time', methods=['GET'])
def get_time():
    return json.dumps({'time': round(datetime.datetime.utcnow().timestamp() * 1000)})

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
