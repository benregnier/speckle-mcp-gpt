from typing import Any, List, Dict, Optional
import os
from mcp.server.fastmcp import FastMCP

# Import Speckle modules
from specklepy.api.client import SpeckleClient

# Initialize FastMCP server
mcp = FastMCP("speckle")

# Global variables
speckle_token = os.environ.get("SPECKLE_TOKEN", "")
speckle_server_url = os.environ.get("SPECKLE_SERVER", "https://app.speckle.systems")

def get_speckle_client() -> SpeckleClient:
    """Initialize and authenticate a Speckle client with the configured token"""
    client = SpeckleClient(host=speckle_server_url)
    if not speckle_token:
        raise ValueError("Speckle token not configured. Please set the SPECKLE_TOKEN environment variable.")
    
    client.authenticate_with_token(speckle_token)
    return client

@mcp.tool()
async def list_projects() -> str:
    """List all projects accessible with the configured Speckle token."""
    try:
        client = get_speckle_client()
        
        # Get the current user's projects
        projects_collection = client.active_user.get_projects()
        
        if not projects_collection or not projects_collection.items:
            return "No projects found for the configured Speckle account."
        
        # Format project information
        project_list = []
        for project in projects_collection.items:
            project_info = f"ID: {project.id}\nName: {project.name}"
            if project.description:
                project_info += f"\nDescription: {project.description}"
            
            project_info += f"\nVisibility: {project.visibility.value}"
            project_info += f"\nCreated: {project.created_at.strftime('%Y-%m-%d')}"
            project_info += f"\nLast Updated: {project.updated_at.strftime('%Y-%m-%d')}"
            
            project_list.append(project_info)
        
        return f"Found {len(project_list)} projects:\n\n" + "\n\n---\n\n".join(project_list)
    
    except Exception as e:
        return f"Error retrieving Speckle projects: {str(e)}"

@mcp.tool()
async def get_project_details(project_id: str) -> str:
    """Get detailed information about a specific Speckle project.
    
    Args:
        project_id: The ID of the Speckle project to retrieve
    """
    try:
        client = get_speckle_client()
        
        # Get the project details
        project = client.project.get(project_id)
        
        if not project:
            return f"No project found with ID: {project_id}"
        
        # Get project models
        project_with_models = client.project.get_with_models(project_id)
        models_count = project_with_models.models.total_count if project_with_models.models else 0
        
        # Get project team
        project_with_team = client.project.get_with_team(project_id)
        team_count = len(project_with_team.team) if project_with_team.team else 0
        
        # Format project details
        details = f"Project: {project.name}\n"
        details += f"ID: {project.id}\n"
        
        if project.description:
            details += f"Description: {project.description}\n"
        
        details += f"Visibility: {project.visibility.value}\n"
        details += f"Created: {project.created_at.strftime('%Y-%m-%d')}\n"
        details += f"Last Updated: {project.updated_at.strftime('%Y-%m-%d')}\n"
        details += f"Models: {models_count}\n"
        details += f"Team Members: {team_count}\n"
        
        if project.source_apps:
            details += f"Source Applications: {', '.join(project.source_apps)}\n"
        
        # Add models if available
        if models_count > 0:
            details += "\nModels:\n"
            for model in project_with_models.models.items:
                details += f"- {model.name} (ID: {model.id})\n"
        
        return details
    
    except Exception as e:
        return f"Error retrieving project details: {str(e)}"

@mcp.tool()
async def search_projects(query: str) -> str:
    """Search for Speckle projects by name or description.
    
    Args:
        query: The search term to look for in project names and descriptions
    """
    try:
        client = get_speckle_client()
        
        # Get all projects first (API doesn't support direct search)
        projects_collection = client.active_user.get_projects()
        
        if not projects_collection or not projects_collection.items:
            return "No projects found for the configured Speckle account."
        
        # Filter projects manually
        matching_projects = []
        for project in projects_collection.items:
            if (query.lower() in project.name.lower() or 
                (project.description and query.lower() in project.description.lower())):
                matching_projects.append(project)
        
        if not matching_projects:
            return f"No projects found matching the search term: '{query}'"
        
        # Format project information
        project_list = []
        for project in matching_projects:
            project_info = f"ID: {project.id}\nName: {project.name}"
            if project.description:
                project_info += f"\nDescription: {project.description}"
            
            project_info += f"\nVisibility: {project.visibility.value}"
            project_list.append(project_info)
        
        return f"Found {len(project_list)} projects matching '{query}':\n\n" + "\n\n---\n\n".join(project_list)
    
    except Exception as e:
        return f"Error searching Speckle projects: {str(e)}"

@mcp.tool()
async def get_model_versions(project_id: str, model_id: str) -> str:
    """Get all versions for a specific model in a project.
    
    Args:
        project_id: The ID of the Speckle project
        model_id: The ID of the model to retrieve versions for
    """
    try:
        client = get_speckle_client()
        
        # Get versions for the specified model
        versions = client.version.get_versions(model_id, project_id)
        
        if not versions or not versions.items:
            return f"No versions found for model {model_id} in project {project_id}."
        
        # Format versions information
        version_list = []
        for version in versions.items:
            version_info = f"Version ID: {version.id}\n"
            version_info += f"Message: {version.message or 'No message'}\n"
            version_info += f"Source Application: {version.source_application or 'Unknown'}\n"
            version_info += f"Created: {version.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            version_info += f"Referenced Object ID: {version.referenced_object}\n"
            
            if version.author_user:
                version_info += f"Author: {version.author_user.name} ({version.author_user.id})\n"
            
            version_list.append(version_info)
        
        return f"Found {len(version_list)} versions for model {model_id}:\n\n" + "\n\n---\n\n".join(version_list)
    
    except Exception as e:
        return f"Error retrieving model versions: {str(e)}"

if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')