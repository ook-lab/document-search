import os

def debug_walk():
    for root, dirs, files in os.walk('.'):
        if '.git' in dirs: dirs.remove('.git')
        if 'node_modules' in dirs: dirs.remove('node_modules')
        for file in files:
            if 'models.yaml' in file:
                print(f'Found models.yaml at: {os.path.join(root, file)}')

if __name__ == '__main__':
    debug_walk()
