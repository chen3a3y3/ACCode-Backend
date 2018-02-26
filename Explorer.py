import os
import magic


FOLDER_T = 'folder'
FILE_T = 'file'
JAVA_T = 'java'
PYTHON_T = 'python'


def list_directory_recursive(path):
    os.chdir(os.path.dirname(__file__))
    tmpDirs = {path: create_dir_data(path, [])}
    for dirname, dirnames, filenames in os.walk(path):

        for subdirname in dirnames:
            dirData = create_dir_data(subdirname, [])
            tmpDirs[os.path.join(dirname, subdirname)] = dirData
            tmpDirs[dirname]['children'].append(dirData)

        for filename in filenames:
            tmpDirs[dirname]['children'].append(create_file_data(filename))

        if 'node_modules' in dirnames:
            dirnames.remove('node_modules')
        elif '.git' in dirnames:
            dirnames.remove('.git')

    return tmpDirs[path]


def list_directory_non_recursive(path):
    files = []
    for filename in os.listdir(path):
        files.append(build_file_data(filename))
    return files


def build_file_data(filename, children=[]):
    if os.path.isfile(filename):
        return create_file_data(filename)
    elif os.path.isdir(filename):
        return create_dir_data(filename, children)


def create_file_data(filename):
    return {'name': filename, 'type': FILE_T}


def create_dir_data(dirname, children=[]):
    return {'name': dirname, 'type': FOLDER_T, 'children': children}


def read_file_content(path):
    if magic.from_file(path, mime=True).split('/')[0] == 'text':
        with open(path) as f:
            return f.read()
    else:
        return "// Sorry, binary file is not supported for editing."


def main():
    list_directory_recursive('../projects/test-android-hello')

if __name__ == '__main__':
    main()
