#!/usr/bin/env python3
"""
Speckle MCP Server

This module provides a Model Context Protocol (MCP) server for interacting with Speckle,
the collaborative data hub that connects with your AEC tools.

The server exposes a set of tools that allow users to:
- List and search Speckle projects
- Retrieve detailed project information
- Access model versions within projects
- Retrieve and query objects and their properties from specific versions

This MCP server acts as a bridge between Speckle's API and client applications,
enabling seamless integration of Speckle's functionality into various workflows.

Environment Variables:
--------------------
- SPECKLE_TOKEN: Your Speckle personal access token (required)
- SPECKLE_SERVER: The Speckle server URL (defaults to https://app.speckle.systems)

Available Tools:
--------------
- list_projects: Lists all accessible Speckle projects
- get_project_details: Retrieves detailed information about a specific project
- search_projects: Searches for projects by name or description
- get_model_versions: Lists all versions for a specific model
- get_version_objects: Retrieves objects from a specific version
- query_object_properties: Queries specific properties from objects in a version

Implementation Details:
---------------------
The server uses a singleton pattern to manage the SpeckleClient instance,
ensuring efficient connection management and authentication handling.
"""

import json
import logging
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Union

# Third-party imports
from mcp.server.fastmcp import FastMCP
from specklepy.api import operations
from specklepy.api.client import SpeckleClient
from specklepy.core.api.inputs.user_inputs import UserProjectsFilter
from specklepy.transports.server import ServerTransport

# Configure logging
logger = logging.getLogger("speckle_mcp")
logger.setLevel(logging.INFO)

# Create console handler
console_handler = logging.StreamHandler(sys.stderr)
console_handler.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(formatter)

# Add handler to logger
logger.addHandler(console_handler)

# Initialize FastMCP server
mcp = FastMCP("speckle")

# Global variables
speckle_token = os.environ.get("SPECKLE_TOKEN", "")
speckle_server_url = os.environ.get("SPECKLE_SERVER", "https://app.speckle.systems")

# Error handling decorator
def handle_exceptions(func: Callable) -> Callable:
    """Decorator for consistent error handling across MCP tools.
    
    This decorator wraps MCP tool functions to provide consistent error handling,
    logging, and formatting of error messages.
    
    Args:
        func: The function to wrap
        
    Returns:
        The wrapped function with error handling
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            logger.info(f"Executing {func.__name__} with args: {args}, kwargs: {kwargs}")
            return await func(*args, **kwargs)
        except Exception as e:
            error_tb = traceback.format_exc()
            error_msg = f"Error in {func.__name__}: {str(e)}"
            
            # Log the full error with traceback
            logger.error(f"{error_msg}\n{error_tb}")
            
            # Return a user-friendly error message
            return f"Error: {str(e)}\n\nFor detailed logs, check the server output."
    
    return wrapper


class SpeckleObjectConverter:
    """A utility class for converting Speckle objects to serializable formats.
    
    This class provides methods to convert complex Speckle objects into
    serializable dictionaries, with options to control recursion depth
    and handle various data types appropriately.
    """
    
    @staticmethod
    def convert_to_dict(obj: Any, depth: int = 0, max_depth: int = 2, include_children: bool = False) -> Any:
        """Convert a Speckle object to a dictionary with depth control.
        
        Args:
            obj: The Speckle object to convert
            depth: Current recursion depth
            max_depth: Maximum recursion depth
            include_children: Whether to include all children objects
            
        Returns:
            A serializable representation of the object
        """
        if depth > max_depth and include_children is False:
            # Limit recursion depth if not including all children
            return {"id": getattr(obj, "id", None), "_type": "reference"}
        
        if hasattr(obj, "__dict__"):
            result = {}
            # Add basic properties
            for key, value in obj.__dict__.items():
                if key.startswith("_"):
                    continue
                    
                if isinstance(value, (str, int, float, bool)) or value is None:
                    result[key] = value
                elif isinstance(value, list):
                    if len(value) > 0:
                        result[key] = [SpeckleObjectConverter.convert_to_dict(item, depth+1, max_depth, include_children) for item in value[:5]]
                        if len(value) > 5:
                            result[key].append({"_note": f"...{len(value)-5} more items"})
                    else:
                        result[key] = []
                elif isinstance(value, dict):
                    result[key] = {k: SpeckleObjectConverter.convert_to_dict(v, depth+1, max_depth, include_children) for k, v in list(value.items())[:5]}
                    if len(value) > 5:
                        result[key]["_note"] = f"...{len(value)-5} more items"
                else:
                    result[key] = SpeckleObjectConverter.convert_to_dict(value, depth+1, max_depth, include_children)
            return result
        elif isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        elif isinstance(obj, list):
            return [SpeckleObjectConverter.convert_to_dict(item, depth+1, max_depth, include_children) for item in obj[:5]]
        elif isinstance(obj, dict):
            return {k: SpeckleObjectConverter.convert_to_dict(v, depth+1, max_depth, include_children) for k, v in list(obj.items())[:5]}
        return str(obj)
    
    @staticmethod
    def convert_value(val: Any) -> Any:
        """Convert a value to a serializable format.
        
        This is a simpler conversion method that doesn't limit recursion depth,
        suitable for converting specific properties.
        
        Args:
            val: The value to convert
            
        Returns:
            A serializable representation of the value
        """
        if hasattr(val, '__dict__'):
            return {k: SpeckleObjectConverter.convert_value(v) for k, v in val.__dict__.items() if not k.startswith('_')}
        elif isinstance(val, list):
            return [SpeckleObjectConverter.convert_value(item) for item in val]
        elif isinstance(val, dict):
            return {k: SpeckleObjectConverter.convert_value(v) for k, v in val.items()}
        elif isinstance(val, (str, int, float, bool)) or val is None:
            return val
        else:
            return str(val)

class SpeckleClientSingleton:
    """Singleton class to manage a single instance of SpeckleClient"""
    _instance = None
    _lock = Lock()
    
    @classmethod
    def get_instance(cls) -> SpeckleClient:
        """Get or create the SpeckleClient instance"""
        with cls._lock:
            if cls._instance is None:
                cls._create_instance()
            
            return cls._instance
    
    @classmethod
    def _create_instance(cls) -> None:
        """Create a new SpeckleClient instance and authenticate it"""
        client = SpeckleClient(host=speckle_server_url)
        if not speckle_token:
            raise ValueError("Speckle token not configured. Please set the SPECKLE_TOKEN environment variable.")
        
        client.authenticate_with_token(speckle_token)
        cls._instance = client
    
    @classmethod
    def refresh_instance(cls) -> SpeckleClient:
        """Force refresh the SpeckleClient instance (useful if token expires)"""
        with cls._lock:
            cls._create_instance()
            return cls._instance

def get_speckle_client() -> SpeckleClient:
    """Get the singleton instance of SpeckleClient
    
    This function handles potential authentication errors by refreshing the client
    if needed. It's a wrapper around the SpeckleClientSingleton to provide additional
    error handling.
    
    Returns:
        An authenticated SpeckleClient instance
        
    Raises:
        ValueError: If the Speckle token is not configured
        Exception: For other authentication or connection errors
    """
    try:
        logger.debug("Getting SpeckleClient instance")
        return SpeckleClientSingleton.get_instance()
    except Exception as e:
        # If there's an authentication error, try refreshing the client
        if "authentication" in str(e).lower() or "token" in str(e).lower():
            logger.warning(f"Authentication issue detected: {str(e)}. Refreshing client...")
            return SpeckleClientSingleton.refresh_instance()
        
        logger.error(f"Failed to get SpeckleClient: {str(e)}")
        raise

@mcp.tool()
@handle_exceptions
async def list_projects() -> str:
    """List all projects accessible with the configured Speckle token."""
    client = get_speckle_client()
    
    # Get the current user's projects
    logger.info("Retrieving user projects")
    projects_collection = client.active_user.get_projects()
    
    if not projects_collection or not projects_collection.items:
        logger.info("No projects found for the configured Speckle account")
        return "No projects found for the configured Speckle account."
    
    # Format project information
    project_list = []
    logger.info(f"Found {len(projects_collection.items)} projects")
    
    for project in projects_collection.items:
        project_info = f"ID: {project.id}\nName: {project.name}"
        if project.description:
            project_info += f"\nDescription: {project.description}"
        
        project_info += f"\nVisibility: {project.visibility.value}"
        project_info += f"\nCreated: {project.created_at.strftime('%Y-%m-%d')}"
        project_info += f"\nLast Updated: {project.updated_at.strftime('%Y-%m-%d')}"
        
        project_list.append(project_info)
    
    return f"Found {len(project_list)} projects:\n\n" + "\n\n---\n\n".join(project_list)

@mcp.tool()
@handle_exceptions
async def get_project_details(project_id: str) -> str:
    """Get detailed information about a specific Speckle project.
    
    Args:
        project_id: The ID of the Speckle project to retrieve
    """
    client = get_speckle_client()
    
    # Get the project details
    logger.info(f"Retrieving details for project: {project_id}")
    project = client.project.get(project_id)
    
    if not project:
        logger.warning(f"No project found with ID: {project_id}")
        return f"No project found with ID: {project_id}"
    
    # Get project models
    logger.info(f"Retrieving models for project: {project_id}")
    project_with_models = client.project.get_with_models(project_id)
    models_count = project_with_models.models.total_count if project_with_models.models else 0
    
    # Get project team
    logger.info(f"Retrieving team for project: {project_id}")
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
    
    logger.info(f"Successfully retrieved details for project: {project_id}")
    return details

@mcp.tool()
@handle_exceptions
async def search_projects(query: str) -> str:
    """Search for Speckle projects by name or description.
    
    Args:
        query: The search term to look for in project names and descriptions
    """
    client = get_speckle_client()
    
    # Use the built-in search_projects functionality of SpeckleClient
    logger.info(f"Searching for projects with query: '{query}'")
    
    # Create a filter with the search term
    filter = UserProjectsFilter(search=query)
    
    # Get projects using the filter
    projects_collection = client.active_user.get_projects(filter=filter)
    
    if not projects_collection or not projects_collection.items:
        logger.info(f"No projects found matching the search term: '{query}'")
        return f"No projects found matching the search term: '{query}'"
    
    # Format project information
    project_list = []
    logger.info(f"Found {len(projects_collection.items)} projects matching '{query}'")
    
    for project in projects_collection.items:
        project_info = f"ID: {project.id}\nName: {project.name}"
        if project.description:
            project_info += f"\nDescription: {project.description}"
        
        project_info += f"\nVisibility: {project.visibility.value}"
        project_list.append(project_info)
    
    return f"Found {len(project_list)} projects matching '{query}':\n\n" + "\n\n---\n\n".join(project_list)

@mcp.tool()
@handle_exceptions
async def get_model_versions(project_id: str, model_id: str) -> str:
    """Get all versions for a specific model in a project.
    
    Args:
        project_id: The ID of the Speckle project
        model_id: The ID of the model to retrieve versions for
    """
    client = get_speckle_client()
    
    # Get versions for the specified model
    logger.info(f"Retrieving versions for model {model_id} in project {project_id}")
    versions = client.version.get_versions(model_id, project_id)
    
    if not versions or not versions.items:
        logger.info(f"No versions found for model {model_id} in project {project_id}")
        return f"No versions found for model {model_id} in project {project_id}."
    
    # Format versions information
    version_list = []
    logger.info(f"Found {len(versions.items)} versions for model {model_id}")
    
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

@mcp.tool()
@handle_exceptions
async def get_version_objects(project_id: str, version_id: str, include_children: bool = False) -> str:
    """Get objects from a specific version in a project.
    
    Args:
        project_id: The ID of the Speckle project
        version_id: The ID of the version to retrieve objects from
        include_children: Whether to include children objects in the response
    """
    client = get_speckle_client()
    
    # Get the version to access its referenced object ID
    logger.info(f"Retrieving version {version_id} from project {project_id}")
    version = client.version.get(version_id, project_id)
    
    if not version:
        logger.warning(f"Version {version_id} not found in project {project_id}")
        return f"Version {version_id} not found in project {project_id}."
    
    # Get the referenced object ID
    object_id = version.referenced_object
    logger.info(f"Referenced object ID: {object_id}")
    
    # Create a server transport to receive the object
    transport = ServerTransport(project_id, client)
    
    # Receive the object
    logger.info(f"Receiving object {object_id}")
    speckle_object = operations.receive(object_id, transport)
    
    # Convert the Speckle object to a serializable dictionary using the converter
    logger.info(f"Converting object to dictionary (include_children={include_children})")
    obj_dict = SpeckleObjectConverter.convert_to_dict(
        speckle_object, 
        max_depth=2, 
        include_children=include_children
    )
    
    # Return basic info and structured data
    result = {
        "version_id": version_id,
        "object_id": object_id,
        "created_at": version.created_at.isoformat(),
        "data": obj_dict
    }
    
    logger.info(f"Successfully retrieved objects for version {version_id}")
    return json.dumps(result, indent=2)

@mcp.tool()
@handle_exceptions
async def query_object_properties(project_id: str, version_id: str, property_path: str) -> str:
    """Query specific properties from objects in a version.
    
    Args:
        project_id: The ID of the Speckle project
        version_id: The ID of the version to retrieve objects from
        property_path: The dot-notation path to the property (e.g., "elements.0.name")
    """
    client = get_speckle_client()
    
    # Get the version to access its referenced object ID
    logger.info(f"Retrieving version {version_id} from project {project_id}")
    version = client.version.get(version_id, project_id)
    
    if not version:
        logger.warning(f"Version {version_id} not found in project {project_id}")
        return f"Version {version_id} not found in project {project_id}."
    
    # Get the referenced object ID
    object_id = version.referenced_object
    logger.info(f"Referenced object ID: {object_id}")
    
    # Create a server transport to receive the object
    transport = ServerTransport(project_id, client)
    
    # Receive the object
    logger.info(f"Receiving object {object_id}")
    speckle_object = operations.receive(object_id, transport)
    
    # Parse the property path
    logger.info(f"Querying property path: {property_path}")
    path_parts = property_path.split('.')
    
    # Navigate through the object structure
    current = speckle_object
    path_so_far = ""
    
    for i, part in enumerate(path_parts):
        path_so_far += ("" if i == 0 else ".") + part
        logger.debug(f"Navigating to part: {part}, path so far: {path_so_far}")
        
        # Handle array indices
        if part.isdigit() and isinstance(current, list):
            index = int(part)
            if index < len(current):
                current = current[index]
            else:
                logger.warning(f"Index {index} out of range at path '{path_so_far}'")
                return f"Error: Index {index} out of range at path '{path_so_far}'"
        # Handle dictionary keys or object attributes
        elif isinstance(current, dict) and part in current:
            current = current[part]
        elif hasattr(current, part):
            current = getattr(current, part)
        elif hasattr(current, '__dict__') and part in current.__dict__:
            current = current.__dict__[part]
        # Handle dynamic attributes with @ prefix
        elif hasattr(current, f'@{part}'):
            current = getattr(current, f'@{part}')
        else:
            logger.warning(f"Property '{part}' not found at path '{path_so_far}'")
            return f"Error: Property '{part}' not found at path '{path_so_far}'"
    
    # Convert the result to a serializable format using the converter
    logger.info(f"Successfully retrieved property at path: {property_path}")
    result = {
        "property_path": property_path,
        "value": SpeckleObjectConverter.convert_value(current)
    }
    
    return json.dumps(result, indent=2)

def main():
    """Main entry point for the Speckle MCP server."""
    try:
        # Log server startup
        logger.info("Starting Speckle MCP server")
        
        # Check for required environment variables
        if not speckle_token:
            logger.error("SPECKLE_TOKEN environment variable is not set")
            print("Error: SPECKLE_TOKEN environment variable is required", file=sys.stderr)
            sys.exit(1)
        
        logger.info(f"Using Speckle server: {speckle_server_url}")
        
        # Initialize and run the server
        logger.info("Initializing MCP server with stdio transport")
        mcp.run(transport='stdio')
    except Exception as e:
        logger.critical(f"Fatal error in Speckle MCP server: {str(e)}\n{traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    main()
