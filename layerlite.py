from __future__ import annotations
import os
import logging
import subprocess
import json
from pathlib import Path
import tarfile
import tempfile
import zipfile
import shutil
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
    create_uv_venv('user_input/requirements_user_file.txt', 'sandbox-env')
    create_uv_venv('user_input/requirements_user_file.txt', 'sandbox-env-backup')
    return measure_venv_size('layerlite_env/sandbox-env')

@tool
def execute_user_file():
    """
    Test user file execution.
    """
    initial_output_result = Path("generated_files/initial_output.json")

    result = subprocess.run(
        ["layerlite_env/sandbox-env/bin/python", 'user_input/user_file.py'],
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
    
    path_env = 'layerlite_env/sandbox-env/'
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
    Save the reduced/cleaned virtual environment as an AWS Lambda deployment package to an S3 bucket.
    Creates a .zip file with the user file and optimized dependencies at the root level, 
    formatted for direct Lambda deployment.
    """
    
    env_path = Path('layerlite_env/sandbox-env')
    site_packages_path = env_path / 'lib/python3.13/site-packages'
    user_file_path = Path('user_input/user_file.py')
    region = os.getenv("AWS_REGION", "us-west-2")
    bucket_name = os.getenv("LAYERLITE_BUCKET", "layerlite-output")
    
    if not env_path.exists():
        return {"error": "Virtual environment not found at layerlite_env/sandbox-env"}
    
    if not site_packages_path.exists():
        return {"error": "Site-packages directory not found in virtual environment"}
        
    if not user_file_path.exists():
        return {"error": "User file not found at user_input/user_file.py"}
    
    try:
        env_metadata = measure_venv_size(str(env_path), detailed=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"lambda_deployment_package_{timestamp}.zip"
        metadata_name = f"lambda_deployment_package_{timestamp}_metadata.json"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_package_dir = Path(temp_dir) / "lambda_package"
            temp_package_dir.mkdir()
            
            shutil.copy2(user_file_path, temp_package_dir / "lambda_function.py")
            
            for item in site_packages_path.iterdir():
                if item.is_dir():
                    shutil.copytree(item, temp_package_dir / item.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, temp_package_dir / item.name)
            
            for root, dirs, files in os.walk(temp_package_dir):
                for d in dirs:
                    os.chmod(os.path.join(root, d), 0o755)
                for f in files:
                    os.chmod(os.path.join(root, f), 0o644)
            
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_archive:
                temp_archive_path = temp_archive.name
                
            with zipfile.ZipFile(temp_archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(temp_package_dir):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(temp_package_dir)
                        zipf.write(file_path, arcname)
        
        zip_size_mb = os.path.getsize(temp_archive_path) / (1024 * 1024)
        
        if zip_size_mb > 250:
            os.unlink(temp_archive_path)
            return {"error": f"Deployment package too large: {zip_size_mb:.2f}MB (Lambda limit: 250MB)"}
        
        metadata = {
            "timestamp": timestamp,
            "archive_name": archive_name,
            "package_type": "lambda_deployment_package",
            "environment_stats": env_metadata,
            "layerlite_version": "v3",
            "compression": "zip",
            "package_size_mb": round(zip_size_mb, 2),
            "lambda_compatible": True,
            "python_version": "3.13",
            "includes_user_code": True,
            "handler_file": "lambda_function.py"
        }
        
        s3_client = boto3.client('s3', region_name=region)
        s3_client.upload_file(
            temp_archive_path,
            bucket_name,
            archive_name,
            ExtraArgs={'ContentType': 'application/zip'}
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
            "package_type": "lambda_deployment_package",
            "archive_url": archive_url,
            "metadata_url": metadata_url,
            "bucket": bucket_name,
            "region": region,
            "package_size_mb": round(zip_size_mb, 2),
            "packages_optimized": len(env_metadata.get('packages', {})),
            "lambda_ready": True,
            "handler": "lambda_function.lambda_handler",
            "timestamp": timestamp
        }
        
        logger.info(f"Lambda deployment package successfully saved to S3: {archive_url}")
        logger.info(f"Package size: {zip_size_mb:.2f}MB (under Lambda 250MB limit)")
        return result
        
    except Exception as e:
        error_msg = f"Failed to create Lambda deployment package: {str(e)}"
        logger.error(error_msg)
        return {"error": error_msg}

model = BedrockModel(
    model_id=os.getenv("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    region_name=os.getenv("AWS_REGION", "us-west-2")
)

try:
    from strands.models.anthropic import AnthropicModel
    model = AnthropicModel(
    client_args={
        "api_key": os.environ.get('ANTHROPIC_KEY', None),
    },
    max_tokens=1028,
    model_id="claude-sonnet-4-20250514",
    params={
        "temperature": 0.7,
    })
except: #since 'strands-agents[anthropic]' is optionnal
    model = None



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
Finally, once all cleaning is done and the environment is working, use `save_env_to_bucket` to save the optimized environment as an AWS Lambda deployment package to an S3 bucket for direct Lambda deployment.
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