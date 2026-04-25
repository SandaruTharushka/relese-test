import os
import sys
import requests
import subprocess
import json

def get_git_remote():
    try:
        output = subprocess.check_output(["git", "remote", "get-url", "origin"]).decode().strip()
        if output.endswith(".git"):
            output = output[:-4]
        if "github.com" in output:
            parts = output.split("github.com/")[-1].split("/")
            return parts[0], parts[1]
    except Exception as e:
        print(f"Error getting git remote: {e}")
    return None, None

def create_release(token, owner, repo, tag, name, body):
    url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "tag_name": tag,
        "name": name,
        "body": body,
        "draft": False,
        "prerelease": False
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 201:
        return response.json()
    else:
        print(f"Error creating release: {response.status_code}")
        print(response.text)
        return None

def upload_asset(token, owner, repo, release_id, file_path):
    file_name = os.path.basename(file_path)
    url = f"https://uploads.github.com/repos/{owner}/{repo}/releases/{release_id}/assets?name={file_name}"
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/octet-stream"
    }
    with open(file_path, "rb") as f:
        response = requests.post(url, headers=headers, data=f)
    
    if response.status_code == 201:
        print(f"Successfully uploaded {file_name}")
    else:
        print(f"Error uploading {file_name}: {response.status_code}")
        print(response.text)

def main():
    if len(sys.argv) < 3:
        print("Usage: python github_release.py <tag> <file1> <file2> ...")
        sys.exit(1)

    tag = sys.argv[1]
    files = sys.argv[2:]

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        # Try to load from .env if available
        try:
            from dotenv import load_dotenv
            load_dotenv()
            token = os.environ.get("GITHUB_TOKEN")
        except ImportError:
            pass

    if not token:
        print("Error: GITHUB_TOKEN environment variable not set.")
        sys.exit(1)

    owner, repo = get_git_remote()
    if not owner or not repo:
        print("Error: Could not determine GitHub owner and repo from git remote.")
        sys.exit(1)

    print(f"Creating release {tag} for {owner}/{repo}...")
    release = create_release(token, owner, repo, tag, f"Release {tag}", f"Automated release for version {tag}")
    
    if release:
        release_id = release["id"]
        for file_path in files:
            if os.path.exists(file_path):
                print(f"Uploading {file_path}...")
                upload_asset(token, owner, repo, release_id, file_path)
            else:
                print(f"Warning: File {file_path} not found.")

if __name__ == "__main__":
    main()
