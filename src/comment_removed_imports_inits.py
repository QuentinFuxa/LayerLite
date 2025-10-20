from pathlib import Path
from src.analyze_recursive_imports import Tree, extract_imports
import ast

def split_imports(code):
    tree = ast.parse(code)
    import_lines = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            dots = '.' * node.level
            module = node.module if node.module else ''
            full_module = f"{dots}{module}"
            new_imports = []
            for alias in node.names:
                name = alias.name
                new_imports.append(f"from {full_module} import {name}")
            import_lines[node.lineno] = new_imports
            if hasattr(node, 'end_lineno') and node.end_lineno:
                for line_num in range(node.lineno + 1, node.end_lineno + 1):
                    import_lines[line_num] = None
        elif isinstance(node, ast.Import):
            new_imports = []
            for alias in node.names:
                new_imports.append(f"import {alias.name}")
            import_lines[node.lineno] = new_imports
            if hasattr(node, 'end_lineno') and node.end_lineno:
                for line_num in range(node.lineno + 1, node.end_lineno + 1):
                    import_lines[line_num] = None
    lines = code.split('\n')
    result = []
    for i, line in enumerate(lines, 1):
        if i in import_lines:
            if import_lines[i] is not None:
                result.extend(import_lines[i])
        else:
            result.append(line)
    return '\n'.join(result)

def single_import_per_line(path: str):
    file_path = Path(path)
    original_text = file_path.read_text()
    
    filename = file_path.name
    backup_path = file_path.parent / f"__INITIAL_{filename}"
    backup_path.write_text(original_text)
    
    splited_text = split_imports(original_text)
    file_path.write_text(splited_text) 

def comment_text(path: str, start_line: int, end_line: int) -> str:
    file_path = Path(path)  
    original_text = file_path.read_text()
    lines = original_text.splitlines()
    for id in range(start_line-1, end_line):
        lines[id] = '#[COMMENTED BY THE JEDI ANALYSIS. THE IMPORT HAS LIKELY BEEN DELETED AND CANNOT BE FOUND] ' + lines[id]
    updated = "\n".join(lines)
    file_path.write_text(updated)
    return "ok"


def find_init_files(lib_path: Path):
    l_inits = []
    for path in lib_path.rglob("__init__.py"):
        l_inits.append(path)
    return l_inits

def get_references(script):
    names = script.get_names()
    n_lines = [name.line for name in names]
    names_references = [name for name in script.get_names(references=True) if name.type == 'module' and name not in names]
    wildcard_imports = [name for name in names_references if name.line not in n_lines]
    line_to_ref = {}
    for name in names_references:
        if line_to_ref.get(name.line) and line_to_ref.get(name.line).column > name.column:
            continue
        line_to_ref[name.line] = name
    return names, wildcard_imports, line_to_ref


def find_broken_imports(path, path_python_exec):
    tree = Tree(path=str(path))
    tree.set_root(environment_path=path_python_exec)
    tree.already_analyzed_paths = set()
    modules = extract_imports(tree).children
    broken_imports = [module for module in modules if module.not_found]
    for broken_import in broken_imports:
        comment_text(path, start_line=broken_import.line, end_line=broken_import.line)
    
    
def restore_init_files_to_initial(folder_path):
    """Restore init files to their initial values using __INITIAL_ backup files"""
    l_files = list(folder_path.rglob("*"))
    all_files = [f for f in l_files if f.is_file()]
    initial_files = [f for f in all_files if '__INITIAL_' in str(f)]
    
    for backup_file in initial_files:
        original_content = backup_file.read_text()
        original_filename = backup_file.name.replace("__INITIAL_", "", 1)
        original_file_path = backup_file.parent / original_filename
        original_file_path.write_text(original_content)
        print(f"Restored {original_file_path} from {backup_file}")

def clean_init_files(lib, path_python_exec):
    l_inits = find_init_files(Path(lib))
    analysis_and_correction_output = []
    for init_file in l_inits:
        print(init_file)
        single_import_per_line(init_file)
        result = find_broken_imports(init_file, path_python_exec)
        analysis_and_correction_output.append(result)
    return analysis_and_correction_output

if __name__ == '__main__':
    lib_location = 'layerlitev3/layerlite_env/demo_env/lib/python3.13/site-packages/' 
    libs_to_analyze = ['scipy', 'pvlib']
    for lib in libs_to_analyze:
        clean_init_files(lib_location + lib)