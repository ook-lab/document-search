import os, re

def revert_models():
    for root, dirs, files in os.walk('.'):
        # Skip .git and node_modules
        if '.git' in dirs: dirs.remove('.git')
        if 'node_modules' in dirs: dirs.remove('node_modules')
        
        for file in files:
            if file.endswith(('.py', '.yaml', '.yml', '.json', '.html', '.sql', '.txt', '.md', '.env')):
                path = os.path.join(root, file)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    
                    new_lines = []
                    changed = False
                    for line in lines:
                        if 'GEMINI_MODEL_HARD' in line:
                            new_lines.append(line)
                        else:
                            new_line = line.replace('gemini-2.5-flash-lite', 'gemini-2.5-flash-lite')
                            if new_line != line:
                                changed = True
                            new_lines.append(new_line)
                    
                    if changed:
                        with open(path, 'w', encoding='utf-8') as f:
                            f.writelines(new_lines)
                        print(f'Updated: {path}')
                except Exception as e:
                    # print(f'Error processing {path}: {e}')
                    pass

if __name__ == '__main__':
    revert_models()
