from __future__ import annotations
import os
import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from typing import Optional

import jedi
from strands import Agent, tool
from strands.models import BedrockModel
from strands.agent.conversation_manager.conversation_manager import ConversationManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INITIAL_OUTPUT_RESULT = Path("generated_files/initial_output.json")
MODIFICATIONS_LOG_PATH = Path("generated_files/llm_modifications_log.json")
USER_FILE = "user_input/user_file.py"
LIB_ROOT_PATH = Path("layerlite_env/demo_env/lib/python3.13/site-packages")

def initialize_modification_log():
    if not MODIFICATIONS_LOG_PATH.exists():
        MODIFICATIONS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        MODIFICATIONS_LOG_PATH.write_text("[]")
        return

def append_log_entry(entry: Dict[str, Any]) -> None:
    initialize_modification_log()
    existing_log = json.loads(MODIFICATIONS_LOG_PATH.read_text() or "[]")
    existing_log.append(entry)
    MODIFICATIONS_LOG_PATH.write_text(json.dumps(existing_log, indent=2))

def log_tool(
        operation: str,
        file_path: Optional[Path] = None,
        details: dict = {},
        ):
    modification_entry = {"operation": operation, "timestamp": datetime.now().isoformat(), "file_path": str(file_path), "details": details}
    print(modification_entry["operation"], modification_entry["file_path"], modification_entry["details"],)
    append_log_entry(modification_entry)

def resolve_within_lib_root(relative_path: str) -> Path:
    """Resolve a relative path within the library root and ensure it doesn't escape it"""
    normalized = relative_path.strip() or "."
    candidate = (LIB_ROOT_PATH / normalized).resolve()
    root = LIB_ROOT_PATH.resolve()
    if not str(candidate).startswith(str(root)):
        raise ValueError(f"Path '{relative_path}' escapes the library root")
    return candidate


def to_lib_relative_path(path: Path) -> str:
    """Return the path relative to the library root"""
    try:
        return str(path.relative_to(LIB_ROOT_PATH))
    except ValueError:
        return str(path)

@tool
def read_file(relative_path: str) -> str:
    """Read file relative to the library root"""
    path = LIB_ROOT_PATH / relative_path
    log_tool(operation='Read File', file_path=path)
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return file_path.read_text()


@tool
def replace_text(relative_path: str, start_line: int, end_line: int, replacement: str) -> str:
    path = LIB_ROOT_PATH / relative_path
    file_path = Path(path)
    
    if not file_path.exists():
        return f"Error: File not found: {path}"
    
    original_text = read_file(path)
    original_lines = original_text.splitlines()
    
    if start_line < 1 or end_line < start_line or start_line > len(original_lines):
        return f"Error: Invalid line range {start_line}-{end_line} for file with {len(original_lines)} lines"
    
    lines = original_lines.copy()
    lines[start_line - 1 : end_line] = replacement.splitlines()
    updated = "\n".join(lines) + ("\n" if original_text.endswith("\n") else "")
    
    file_path.write_text(updated)
    
    log_tool(
        operation= 'Replacement',
        file_path=file_path,
        details={
            'start_line': start_line,
            'end_line': end_line,
        }
    )
    return "done"

@tool
def check_syntax_file(relative_path: str):
    """Check if syntax of given file is correct. Path is relative to library root"""
    path = LIB_ROOT_PATH / relative_path
    log_tool(operation= 'Check syntax', file_path=path,)
    script = jedi.Script(path=path)
    errors = script.get_syntax_errors()
    return errors

@tool
def search_lib_items(query: str, item_type: str = "any", max_results: int = 50) -> Dict[str, Any]:
    """Search for files or directories under the library root by partial name match"""
    logger.info(f"Search lib items query={query!r} type={item_type}")
    normalized_type = (item_type or "any").lower()
    if normalized_type == "folder":
        normalized_type = "directory"
    allowed_types = {"any", "file", "directory"}

    if normalized_type not in allowed_types:
        message = f"Invalid item_type '{item_type}'. Allowed values: any, file, directory"
        log_tool(operation="[Error] Search lib", details={"query": query, "item_type": normalized_type, "error": message})
        return {"error": message, "allowed_types": sorted(allowed_types)}

    if not LIB_ROOT_PATH.exists():
        message = f"Library root not found at {LIB_ROOT_PATH}"
        log_tool(operation="[Error] Search lib", details={"query": query, "item_type": normalized_type, "error": message})
        return {"error": message}

    try:
        max_results_int = int(max_results)
    except (TypeError, ValueError):
        max_results_int = 50
    max_results_int = max(1, min(max_results_int, 500))

    query_lower = (query or "").lower()
    results = []

    for root, dirs, files in os.walk(LIB_ROOT_PATH):
        root_path = Path(root)

        if normalized_type in ("any", "directory"):
            for dirname in dirs:
                if query_lower in dirname.lower():
                    full_path = root_path / dirname
                    results.append(
                        {
                            "type": "directory",
                            "name": dirname,
                            "relative_path": to_lib_relative_path(full_path),
                        }
                    )
                    if len(results) >= max_results_int:
                        break
            if len(results) >= max_results_int:
                break

        if normalized_type in ("any", "file") and len(results) < max_results_int:
            for filename in files:
                if query_lower in filename.lower():
                    full_path = root_path / filename
                    results.append(
                        {
                            "type": "file",
                            "name": filename,
                            "relative_path": to_lib_relative_path(full_path),
                        }
                    )
                    if len(results) >= max_results_int:
                        break
        if len(results) >= max_results_int:
            break

    summary_details = {"query": query, "item_type": normalized_type, "results_count": len(results), "results_preview": results[:5], "max_results": max_results_int,}
    log_tool(operation="Search lib", details=summary_details)
    return {
        "query": query,
        "item_type": normalized_type,
        "max_results": max_results_int,
        "results": results,
    }


@tool
def inspect_lib_directory(relative_path: str = ".") -> Dict[str, Any]:
    """List immediate directory contents under the library root"""
    logger.info(f"Inspect lib directory {relative_path}")
    try:
        target_path = resolve_within_lib_root(relative_path)
    except ValueError as err:
        details = {"relative_path": relative_path, "error": str(err)}
        log_tool(operation="[ERROR] Inspect lib", details=details)
        return {"error": str(err)}

    if not target_path.exists():
        message = f"Path '{relative_path}' does not exist"
        details = {"relative_path": relative_path, "error": message}
        log_tool(operation="[ERROR] Inspect lib", details=details)
        return {"error": message}

    if not target_path.is_dir():
        message = f"Path '{relative_path}' is not a directory"
        details = {"relative_path": relative_path, "error": message}
        log_tool(operation="[ERROR] Inspect lib", details=details)
        return {"error": message}

    directories = sorted(
        [child.name for child in target_path.iterdir() if child.is_dir()]
    )
    files = sorted([child.name for child in target_path.iterdir() if child.is_file()])
    relative_target = to_lib_relative_path(target_path)

    details = {"relative_path": relative_target, "directories": directories[:10], "files": files[:10], "total_directories": len(directories), "total_files": len(files)}
    log_tool(operation="Inspect lib", details=details)
    return {
        "relative_path": relative_target,
        "directories": directories,
        "files": files,
    }


@tool
def move_lib_item(source_relative_path: str, destination_relative_path: str) -> str:
    """Move or rename a file or directory within the library root"""
    logger.info(f"Move lib item {source_relative_path} -> {destination_relative_path}")
    error_message = None
    try:
        source_path = resolve_within_lib_root(source_relative_path)
        destination_path = resolve_within_lib_root(destination_relative_path)
    except ValueError as err:
        error_message = str(err)

    if not error_message and not source_path.exists():
        error_message = f"Source '{source_relative_path}' does not exist"
        
    if not error_message and destination_path.exists():
        error_message = f"Destination '{destination_relative_path}' already exists"
        
    if error_message:
        log_tool(operation="Move item Error", file_path=source_path, details={'destination': str(destination_path), 'error': error_message})
        return f"Error: {error_message}"
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(destination_path))

    log_tool("Move item", file_path=source_path, details={'destination': str(destination_path)})
    return f"Moved '{source_relative_path}' to '{destination_relative_path}'"

@tool
def execute_user_file():
    """
    Use that to test if user file still works
    """
    result = subprocess.run(
        ["layerlite_env/demo_env/bin/python", USER_FILE],
        capture_output=True,
        text=True
    )
    log_tool("Execute user file", details={'stdout': str(result.stdout), "stderr": result.stderr, "returncode": result.returncode})
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode
    }

@tool
def execute_initial_user_file():
    with open(INITIAL_OUTPUT_RESULT) as f:
        content = f.read()
    return content

model = BedrockModel(
    model_id=os.getenv("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    region_name=os.getenv("AWS_REGION", "us-west-2")
)

agent_cleanup = Agent(
    tools=[
        execute_initial_user_file,
        read_file,
        replace_text,
        execute_user_file,
        search_lib_items,
        inspect_lib_directory,
        move_lib_item,
    ],
    system_prompt=f"""
        You participate in LayerLite, a solution to reduce the size of python packages.
        A recursive static analysis of files actually used in the library has been made, starting by the user file calls in {USER_FILE}.
        Files considered useless have a new prefix __DELETED_. This cleaning has been made using Jedi library.
        Unfortunatly, the cleaning may have make the package not usable: 
            - Some not used file may still be referecended in the __init__.py files, some compiled or data files may have been deleted...
            - Some not used - and removed - subpackaged may still be import dynamicly, using importlib. 
        You can use `execute_initial_user_file` to see what to except/what is the target, and `execute_user_file` to see the stdout and stderr with the current state of the library.        
        Work on the files until `execute_user_file` works. 
        We are confident in the cleaning that has been made by Jedi, for the .py files. Some compiled files may still have been deleted unexexpectadly.
        So:
            - If some imports fail, wonder: is the file necessary ? Should I remove the import reference ? 
                - For example, if a file uses importlib, 
                    - Use `read_file` to look at file content. 
                    - Use `inspect_lib_directory` to check if submodule still exist
                    - Use`replace_text` and to comment/adjust code and remove these imports
            - If you think files have to be restored - especially compiled/not python file-, use move_lib_item to remove the __DELETED_prefix. 
            - Once the user script works, tell us, so that we can definitly delete the __DELETED_ files.
    """,
    model=model,
    conversation_manager=ConversationManager
)


if __name__ == "__main__":
    response = agent_cleanup("Solve issues")