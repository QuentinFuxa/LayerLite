from __future__ import annotations
import os
import logging
import subprocess
import json
from pathlib import Path
import tarfile
import tempfile
import json
from datetime import datetime

import boto3
from strands import Agent, tool
from bedrock_agentcore import BedrockAgentCoreApp

from src.create_venv import create_uv_venv, measure_venv_size
from src.analyze_recursive_imports import Tree, recursive_analysis, extract_used_files, virtual_remove_unused_files, compute_virtual_gained_size
from src.comment_removed_imports_inits import clean_init_files
from src.agent_cleanup_package import agent_cleanup
from strands.models import BedrockModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

@tool
def save_user_file(content: str):
    os.makedirs('user_input', exist_ok=True)    
    file_path = 'user_input/user_file.py'
    
    try:
        with open(file_path, 'w') as f:
            f.write(content)
            return f"File saved successfully to: {file_path}"
    except Exception as e:
        print(str(e))
        return str(e)
        

@tool
def create_venv_from_requirements(list_of_requirements:list):
    """
    Example: list_of_requirements=['numpy', 'pandas']
    """
    requirements = "\n".join(list_of_requirements)
    with open('user_input/requirements_user_file.txt', 'w') as f:
        f.write(requirements)
    create_uv_venv('user_input/requirements_user_file.txt', 'env-strands')
    create_uv_venv('user_input/requirements_user_file.txt', 'env-strands-backup')
    return measure_venv_size('layerlite_env/env-strands')

@tool
def execute_user_file():
    """
    Test user file execution.
    """
    initial_output_result = Path("generated_files/initial_output.json")

    result = subprocess.run(
        ["layerlite_env/env-strands/bin/python", 'user_input/user_file.py'],
        capture_output=True,
        text=True
    )
    output_data = {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode
    }
    Path("generated_files/initial_output.json")
    initial_output_result.parent.mkdir(parents=True, exist_ok=True)

    with open(initial_output_result, "w") as f:
        json.dump(output_data, f, indent=2)
    return output_data

@tool
def run_main_pipeline(libs_to_analyze: list):
    """
    Example: libs_to_analyze=['scipy', 'pvlib'].
    The names should be the names of the dir in site-packages, not the name from PIP.
    """
    
    path_env = 'layerlite_env/env-strands/'
    path_python_exec = path_env + 'bin/python3'
    path_libs = path_env + 'lib/python3.13/site-packages/'
    try:
        tree = Tree(path='user_input/user_file.py')
        tree.set_root(environment_path=path_python_exec)
        resulting_tree = recursive_analysis(tree, libs_to_analyze)
        resulting_tree.stub_add_compiled_file()
        dpaths = extract_used_files(resulting_tree)
        lib_gained_result = []
        for lib in libs_to_analyze:
            virtual_remove_unused_files(path_libs + lib, dpaths[lib])
            clean_init_files(path_libs + lib, path_python_exec=path_python_exec)
            lib_gained_result.append({'lib': lib,
                'result': compute_virtual_gained_size(path_libs + lib)
            }
            )
    except Exception as e:
        print('ERROR:', str(e))
        return str(e)
    print('Gains:', lib_gained_result)
    return lib_gained_result

@tool
def clean_packages_agent() -> str:
    response = agent_cleanup("Solve issues")
    return str(response)

@tool
def save_env_to_bucket():
    """
    Save the reduced/cleaned virtual environment to an S3 bucket.
    Creates a compressed archive of the optimized environment and uploads it with metadata.
    """
    
    env_path = Path('layerlite_env/env-strands')
    region = os.getenv("AWS_REGION", "us-west-2")
    bucket_name = os.getenv("LAYERLITE_BUCKET", "layerlite-optimized-envs")
    
    if not env_path.exists():
        return {"error": "Virtual environment not found at layerlite_env/env-strands"}
    
    try:
        env_metadata = measure_venv_size(str(env_path), detailed=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"layerlite_env_{timestamp}.tar.gz"
        metadata_name = f"layerlite_env_{timestamp}_metadata.json"
        
        with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as temp_archive:
            with tarfile.open(temp_archive.name, 'w:gz') as tar:
                tar.add(env_path, arcname='env-strands')
            temp_archive_path = temp_archive.name
        
        metadata = {
            "timestamp": timestamp,
            "archive_name": archive_name,
            "environment_stats": env_metadata,
            "layerlite_version": "v3",
            "compression": "gzip"
        }
        
        s3_client = boto3.client('s3', region_name=region)
        s3_client.upload_file(
            temp_archive_path,
            bucket_name,
            archive_name,
            ExtraArgs={'ContentType': 'application/gzip'}
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_metadata:
            json.dump(metadata, temp_metadata, indent=2)
            temp_metadata_path = temp_metadata.name
        
        s3_client.upload_file(
            temp_metadata_path,
            bucket_name,
            metadata_name,
            ExtraArgs={'ContentType': 'application/json'}
        )
        os.unlink(temp_archive_path)
        os.unlink(temp_metadata_path)
        archive_url = f"s3://{bucket_name}/{archive_name}"
        metadata_url = f"s3://{bucket_name}/{metadata_name}"
        
        result = {
            "success": True,
            "archive_url": archive_url,
            "metadata_url": metadata_url,
            "bucket": bucket_name,
            "region": region,
            "environment_size_mb": env_metadata.get('total_size_mb', 0),
            "packages_optimized": len(env_metadata.get('packages', {})),
            "timestamp": timestamp
        }
        
        logger.info(f"Environment successfully saved to S3: {archive_url}")
        return result
        
    except Exception as e:
        error_msg = f"Failed to save environment to bucket: {str(e)}"
        logger.error(error_msg)
        return {"error": error_msg}

model = BedrockModel(
    model_id=os.getenv("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    region_name=os.getenv("AWS_REGION", "us-west-2")
)

agent = Agent(
    tools=[
        save_user_file,
        create_venv_from_requirements,
        run_main_pipeline,
        execute_user_file,
        clean_packages_agent,
        save_env_to_bucket
    ],
    system_prompt="""
You are LayerLite, an application whose objective is to reduce the size of python packages.
The user will give you a script to optimize.
First, save the user file using `save_user_file`.
Then, extract from the script the requirements needed to execute it. Use `create_venv_from_requirements` to create an environement with the packages you extracted. it will return stats on the installed packages, and determine which packaged are the most interesting to optimize.
Most interesting packages are the one with the bigger size. LayerLite can remove both python and non python files, so you can just take the top - that is non installed by default. 
Finally execute `execute_user_file` to check if the user file works in the created environement.
If execution fails:
-   If you think that it has failed because of missing packages, recreate the environement.
-   If you think that it has failed because the user code is not viable, tell him, and ask him to give you a functionnal file.
If it worked correctly, indicate to the user which packages you suggest to optimize.

Once the user has validated the packages to trim, use `run_main_pipeline` and describe the user the gain that have been made. Ask user if you can continue the cleaning.
The cleaning has probably broke things. Use `clean_packages_agent` to try to obtain functionnal librairies.
Finally, once all cleaning is done and the environment is working, use `save_env_to_bucket` to save the optimized environment to an S3 bucket for future use.
    """,
    model=model,
)

@app.entrypoint
def main(payload):
    """Main entrypoint - handles user messages."""
    user_message = payload.get("prompt", "Run the pipeline on the user file.")    
    if not user_message or not user_message.strip():
        user_message = "Hello! I'm LayerLite, ready to help you optimize your Python packages. Please provide a Python script that you'd like to optimize."
    
    try:
        response = agent(user_message)
        return {"message": response.message}
    except Exception as e:
        logger.error(f"Error processing user message: {str(e)}")
        return {"message": "I encountered an error processing your request. Please try again or provide a valid Python script to optimize."}

if __name__ == "__main__":
    app.run()
    # create_uv_venv('user_input/requirements_user_file.txt', 'env-strands')
