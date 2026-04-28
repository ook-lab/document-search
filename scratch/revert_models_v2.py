import os, re

def revert_models():
    for root, dirs, files in os.walk('.'):
        if '.git' in dirs: dirs.remove('.git')
        if 'node_modules' in dirs: dirs.remove('node_modules')
        
        for file in files:
            if file.endswith(('.py', '.yaml', '.yml', '.json', '.html', '.sql', '.txt', '.md', '.env')):
                path = os.path.join(root, file)
                try:
                    # Try different encodings
                    content = None
                    for enc in ['utf-8', 'shift_jis', 'cp932', 'latin-1']:
                        try:
                            with open(path, 'r', encoding=enc) as f:
                                content = f.read()
                            break
                        except:
                            continue
                    
                    if content is None:
                        print(f'Failed to read: {path}')
                        continue
                    
                    lines = content.splitlines(keepends=True)
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
                    print(f'Error processing {path}: {e}')

if __name__ == '__main__':
    revert_models()
