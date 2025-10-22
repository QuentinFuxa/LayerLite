from __future__ import annotations
import jedi
from jedi.api.environment import Environment
from dataclasses import dataclass, field
import pickle
import os
import shutil
from typing import Optional, ClassVar
from pathlib import Path

@dataclass
class Tree():
    depth: int = 0
    name: Optional[str] = None
    path: Optional[str] = None
    children: set = field(default_factory=set)
    parent: Optional[Tree] = None
    not_found: bool = False
    probable_paths: list = field(default_factory=list)
    module: Optional[str] = None
    is_stub: bool = False
    line: Optional[int] = None
    is_wildcard: bool = False
    other_parents: list = field(default_factory=list) #to prevent cycles/memory leaks we only store the paths of other parents, not the trees.
    has_been_analyzed: bool = False

    initial_path: ClassVar[str] = ""
    environment: ClassVar[Optional[Environment]] = None
    
    def set_root(self, environment_path: str):
        Tree.initial_path = self.path
        Tree.environment = Environment(environment_path)
    
    def __hash__(self):
        return hash((self.name, self.path))
    
    def get_all_nodes(self):
        l_children = [self]
        if self.children:
            for child in self.children:
                l_children.extend(
                    child.get_all_nodes()
                )
        return l_children

    def get_all_paths(self):
        """to rewrite"""
        l_probable_paths = []
        if self.not_found:
            return [], self.probable_paths
        l_paths = [self.path]
        if self.children:
            for child in self.children:
                l_children_path, l_children_probable_paths = child.get_all_paths()
                l_paths.extend(l_children_path)
                l_probable_paths.extend(l_children_probable_paths)
        return l_paths, l_probable_paths
    
    def search_nodes_fuzzy(self, name: str):
        l_result = []
        if self.name and name in self.name:
            return [self]
        elif self.path and name in self.path:
            return [self]
        elif self.children:
            for child in self.children:
                l_result.extend(child.search_nodes_fuzzy(name))
        return l_result
                
    def to_root(self):
        if self.parent:
            return self.parent.to_root()
        else:
            return self
    
    
    def search_node(self, path: str):
        if path == self.path:
            return self
        for child in self.children:
            corresponding_tree = child.search_node(path)
            if corresponding_tree:
                return corresponding_tree
        return False   
        
    
    def guess_probable_path(self):
        path_not_found = []
        path_found = []
        if self.not_found and self.parent:
            dirname = os.path.dirname(self.parent.path)
            files = [os.path.join(dirname, f) for f in os.listdir(dirname) if f.startswith(self.name)]
            module_files = [os.path.join(dirname, f) for f in os.listdir(dirname) if self.module and f.startswith(self.module)]
            if files:
                for file in files:
                    if os.path.isdir(file):
                        all_files = []
                        for root, dirs, files in os.walk(file):
                            for file in files:
                                all_files.append(os.path.join(root, file))
                        self.probable_paths.extend(all_files)
                    else:
                        self.probable_paths.append(file)
                    path_found.append(self)
            elif module_files:
                for file in module_files:
                    if os.path.isdir(file):
                        all_files = []
                        for root, dirs, files in os.walk(file):
                            for file in files:
                                all_files.append(os.path.join(root, file))
                        self.probable_paths.extend(all_files)
                    else:
                        self.probable_paths.append(file)
                path_found.append(self)
            else:
                path_not_found.append(self)
        elif self.children:
            for child in self.children:
                d_paths = child.guess_probable_path()
                path_not_found.extend(d_paths['path_not_found'])
                path_found.extend(d_paths['path_found'])
        return {
            'path_not_found': path_not_found,
            'path_found': path_found
        }

    def stub_add_compiled_file(self):
        all_paths = self.probable_paths.copy()
        if self.path:
            all_paths.append(self.path)
        for path in all_paths:
            extension = path.split('.')[-1]
            if extension == 'pyi':
                dirname = os.path.dirname(path)
                filename = os.path.basename(path)
                files = [os.path.join(dirname, f) for f in os.listdir(dirname) if f.startswith(filename.split('.')[0]) and f != filename ]
                self.is_stub = True
                for file in files:
                    self.parent.children.add(
                        Tree(
                            depth=self.depth,
                            name=self.name,
                            path=file,
                            parent=self.parent,
                        )
                    )
        if self.children:
            initial_contains = list(self.children)
            for child in initial_contains:
                child.stub_add_compiled_file()
    
    def get_wildcard_names(self):
        wildcards = set()
        if self.is_wildcard:
            wildcards.add(self)
        for child in self.children:
            wildcards = wildcards | child.get_wildcard_names()
        return wildcards
    
    def should_analyze(self, libs_to_analyze):
        if self.path and not self.has_been_analyzed:
            if any(lib in self.path for lib in libs_to_analyze) or self.path==Tree.initial_path and not self.path.endswith('__init__.py'):
                return True
        return False
    
    def get_references(self):
        parent_path = self.path
        script = jedi.Script(path=parent_path, environment=Tree.environment)
        names = script.get_names(all_scopes=False) #all scopes is needed if an import is done inside a function
        n_lines = [name.line for name in names]
        names_references = [name for name in script.get_names(references=True) if name.type == 'module' and name not in names]
        wildcard_imports = [name for name in names_references if name.line not in n_lines and not has_parentheses(parent_path, name.line)]
        line_to_ref = {}
        for name in names_references:
            if line_to_ref.get(name.line) and line_to_ref.get(name.line).column > name.column:
                continue
            line_to_ref[name.line] = name
        return names, wildcard_imports, line_to_ref


def has_parentheses(file_path: str, line_number: int) -> bool:
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    line = lines[line_number - 1]
    return '(' in line or ')' in line

# def extract_import_lines(file_path: str):
#     with open(file_path, 'r', encoding='utf-8') as f:
#         lines = f.readlines()
#     for line in lines:
#         els = line.split('import')
#         if len(els) > 1:
#             pre_import = els[0]
#             post_import = els[1]
#             pre_import.split('from')

def explore_name_definitions(tree, name, parent_path, line_to_ref, is_wildcard):
    parent_path = tree.path
    definitions = name.goto(follow_imports=True, follow_builtin_imports=True) #goto can return an __init__ . .infer probably wont
    children = set()
    if definitions: 
        for definition in definitions:
            if definition.module_path:
                definition_path = str(definition.module_path)
                if definition_path != parent_path:
                    node = tree.to_root().search_node(path=definition_path)
                    if not node:
                        children.add(Tree(
                                depth=tree.depth + 1,
                                path=definition_path,
                                name=definition.full_name,
                                module=line_to_ref[name.line].name if line_to_ref.get(name.line) else None,
                                line=name.line,
                                parent=tree,
                                is_wildcard=is_wildcard
                                )
                        )
                    else:
                        # children.add(node) #introduces memory leak.
                        node.other_parents.append(parent_path)
            elif name.type == 'module':
                children.add(
                    Tree(
                        depth=tree.depth + 1,
                        name=name.name,
                        not_found=True,
                        module=line_to_ref[name.line].name if line_to_ref.get(name.line) else None,
                        line=name.line,
                        is_wildcard=is_wildcard,
                        parent=tree
                    )                
                )
    else:
        children.add(
            Tree(
                depth=tree.depth + 1,
                name=name.name,
                not_found=True,
                module=line_to_ref[name.line].name if line_to_ref.get(name.line) else None,
                line=name.line,
                is_wildcard=is_wildcard,
                parent=tree
            )                
        )    
    return children

def extract_imports(tree):
    parent_path = tree.path
    if parent_path:        
        names, wildcard_imports, line_to_ref = tree.get_references()
        source_files = set()
        for name in names:
            source_files = source_files | explore_name_definitions(tree, name, parent_path, line_to_ref, is_wildcard=False)
        for wildcard_import in wildcard_imports:
            source_files = source_files | explore_name_definitions(tree, wildcard_import, parent_path, line_to_ref, is_wildcard=True)
        tree.children = source_files
    return tree

def recursive_analysis(tree, libs_to_analyze):
    if tree.path.endswith('__init__.py'):
        return tree
    print(tree.depth, tree.name if tree.name else tree.path)
    tree = extract_imports(tree)
    for children_tree in tree.children:
        if children_tree.should_analyze(libs_to_analyze=libs_to_analyze):
            children_tree = recursive_analysis(children_tree, libs_to_analyze)
            children_tree.has_been_analyzed = True
    return tree


def extract_used_files(resulting_tree):
    l_paths, l_probable_paths = resulting_tree.get_all_paths()
    l_paths.extend(l_probable_paths)
    dpaths = {}
    for _path in sorted(set(l_paths)):
        position = dpaths
        arbo = _path.split('site-packages/')
        if len(arbo) > 1:
            arbo = arbo[1].split('/')
            for i in arbo:
                if i == arbo[-1]:
                    position.setdefault(i, 'FILE')
                else:
                    position.setdefault(i, {})
                position = position[i]
    return dpaths

def has_file(arbo):
    if 'FILE' in str(arbo):
        return True
    return False
                
def virtual_remove_unused_files(src_folder, to_keep):
    for el in os.listdir(src_folder):
        if (el == '__init__.py' and has_file(to_keep)) or '__DELETED_' in el:
            continue
        full_path = os.path.join(src_folder, el)
        backup_path = os.path.join(src_folder, '__DELETED_' + el)
        is_dir = os.path.isdir(full_path)
        if is_dir:
            if not to_keep.get(el):
                to_keep[el] = {}
            virtual_remove_unused_files(os.path.join(src_folder, el), to_keep[el])
        elif not to_keep.get(el, False):
            shutil.copy2(full_path, backup_path)
            os.remove(full_path)
            to_keep[el] = 'DELETED'
            
def re_add_virtualy_removed_files(folder_path):
    l_files = list(folder_path.rglob("*"))
    all_files = [f for f in l_files if f.is_file()]
    deleted_files = [f for f in all_files if '__DELETED_' in str(f)]
    for file in deleted_files:
        new_name = file.name.replace("__DELETED_", "", 1)
        file.rename(file.with_name(new_name))
        pass

def compute_virtual_gained_size(folder_path):

    l_files = list(Path(folder_path).rglob("*"))
    all_files = [f for f in l_files if f.is_file()]
    deleted_files = [f for f in all_files if '__DELETED_' in str(f)]

    size_all = sum(f.stat().st_size for f in all_files)
    size_deleted = sum(f.stat().st_size for f in deleted_files)
    size_kept = size_all - size_deleted

    to_mb = lambda s: s / (1024 * 1024)
    deleted_pct = 100 * len(deleted_files) / len(all_files) if all_files else 0
    size_reduction_pct = 100 * size_deleted / size_all if size_all else 0

    print(f"  - Total files: {len(all_files)}")
    print(f"  - Deleted files: {len(deleted_files)} ({deleted_pct:.1f}%)")
    print(f"  - Total size before: {to_mb(size_all):.2f} MB")
    print(f"  - Total size after:  {to_mb(size_kept):.2f} MB")
    print(f"  - Size reduction:    {to_mb(size_deleted):.2f} MB ({size_reduction_pct:.1f}%)")
    return {
        'Total files': len(all_files), 
        'Deleted files': f"{len(deleted_files)} ({deleted_pct:.1f}%)",
        'Total size before': f"{to_mb(size_all):.2f} MB",
        'Total size after': f"{to_mb(size_kept):.2f} MB",
        'Size reduction': f"{to_mb(size_deleted):.2f} MB ({size_reduction_pct:.1f}%)"                                               
    }


if __name__ == '__main__':
    ANALYZE = True
    SAVE = True 
    DELETE = True
    RESTORE = False
    output_tree_path = "generated_files/resulting_tree_both.pickle"
    output_json_path = "generated_files/lib_structure.json"

    libs_to_analyze = ['scipy', 'pvlib']
    initial_path = "user_input/user_file.py"
    path_env = 'layerlite_env/env-strands/'
    path_python_exec = path_env + 'bin/python3'
    path_libs = path_env + 'lib/python3.13/site-packages/'
    
    path_python_exec = path_env + 'bin/python3'
    if RESTORE:
        for lib in libs_to_analyze:
            re_add_virtualy_removed_files(path_libs + lib)
    else:
        if ANALYZE:
            tree = Tree(path=initial_path)
            tree.set_root(environment_path=path_python_exec)
            resulting_tree = recursive_analysis(tree, libs_to_analyze)
            if SAVE:
                resulting_tree.environment = None 
                with open(output_tree_path, 'wb') as handle:
                    pickle.dump(resulting_tree, handle, protocol=pickle.HIGHEST_PROTOCOL)
        else:
            with open(output_tree_path, 'rb') as handle:
                resulting_tree = pickle.load(handle)
        wildcard_names= resulting_tree.get_wildcard_names()
        found_and_not_found = resulting_tree.guess_probable_path()
        resulting_tree.stub_add_compiled_file()
        
        dpaths = extract_used_files(resulting_tree)
    for lib in libs_to_analyze:
        if not RESTORE and DELETE:
            virtual_remove_unused_files(path_libs + lib, dpaths[lib])
        print(compute_virtual_gained_size(path_libs + lib))