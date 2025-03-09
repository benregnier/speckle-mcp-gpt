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
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

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

# Utility functions
def format_datetime(dt: datetime, include_time: bool = False) -> str:
    """Format a datetime object consistently throughout the application.
    
    Args:
        dt: The datetime object to format
        include_time: Whether to include time in the formatted string
        
    Returns:
        A formatted datetime string
    """
    if include_time:
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    return dt.strftime('%Y-%m-%d')

def get_property_by_path(obj: Any, path: str) -> Tuple[Any, Optional[str]]:
    """Navigate through an object structure using a dot-notation path.
    
    Args:
        obj: The object to navigate
        path: The dot-notation path to the property (e.g., "elements.0.name")
        
    Returns:
        A tuple containing (result_value, error_message)
        If successful, error_message will be None
    """
    path_parts = path.split('.')
    current = obj
    path_so_far = ""
    
    for i, part in enumerate(path_parts):
        path_so_far += ("" if i == 0 else ".") + part
        
        # Handle array indices
        if part.isdigit() and isinstance(current, list):
            index = int(part)
            if index < len(current):
                current = current[index]
            else:
                return None, f"Index {index} out of range at path '{path_so_far}'"
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
            return None, f"Property '{part}' not found at path '{path_so_far}'"
    
    return current, None

def truncate_collection(collection: Union[List, Dict], limit: int = 5) -> Union[List, Dict]:
    """Truncate a collection (list or dict) to a specified limit.
    
    Args:
        collection: The collection to truncate
        limit: Maximum number of items to keep
        
    Returns:
        Truncated collection with a note about omitted items
    """
    if isinstance(collection, list):
        if len(collection) <= limit:
            return collection
        result = collection[:limit].copy()
        if len(collection) > limit:
            result.append({"_note": f"...{len(collection)-limit} more items"})
        return result
    
    elif isinstance(collection, dict):
        if len(collection) <= limit:
            return collection
        result = dict(list(collection.items())[:limit])
        if len(collection) > limit:
            result["_note"] = f"...{len(collection)-limit} more items"
        return result
    
    return collection

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
    def convert_to_dict(speckle_object: Any, depth: int = 0, max_depth: int = 2, include_children: bool = False) -> Any:
        """Convert a Speckle object to a dictionary with depth control.
        
        Args:
            speckle_object: The Speckle object to convert
            depth: Current recursion depth
            max_depth: Maximum recursion depth
            include_children: Whether to include all children objects
            
        Returns:
            A serializable representation of the object
        """
        # Try to use the built-in to_dict method if available
        if hasattr(speckle_object, "to_dict") and callable(getattr(speckle_object, "to_dict")):
            try:
                # Use the built-in method and post-process the result
                result = speckle_object.to_dict()
                # Apply depth limiting and truncation
                return SpeckleObjectConverter._process_dict_result(result, depth, max_depth, include_children)
            except Exception as e:
                logger.warning(f"Error using built-in to_dict: {str(e)}. Falling back to custom conversion.")
                # Fall back to custom conversion if the built-in method fails
        
        # Depth limiting
        if depth > max_depth and include_children is False:
            # Limit recursion depth if not including all children
            return {"id": getattr(speckle_object, "id", None), "_type": "reference"}
        
        # Custom conversion logic
        if hasattr(speckle_object, "__dict__"):
            result = {}
            # Add basic properties
            for key, value in speckle_object.__dict__.items():
                if key.startswith("_"):
                    continue
                    
                result[key] = SpeckleObjectConverter._process_value(value, depth, max_depth, include_children)
            return result
        elif isinstance(speckle_object, (str, int, float, bool)) or speckle_object is None:
            return speckle_object
        elif isinstance(speckle_object, list):
            return SpeckleObjectConverter._process_list(speckle_object, depth, max_depth, include_children)
        elif isinstance(speckle_object, dict):
            return SpeckleObjectConverter._process_dict(speckle_object, depth, max_depth, include_children)
        return str(speckle_object)
    
    @staticmethod
    def _process_value(value: Any, depth: int, max_depth: int, include_children: bool) -> Any:
        """Process a value based on its type.
        
        Args:
            value: The value to process
            depth: Current recursion depth
            max_depth: Maximum recursion depth
            include_children: Whether to include all children objects
            
        Returns:
            Processed value
        """
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        elif isinstance(value, list):
            return SpeckleObjectConverter._process_list(value, depth, max_depth, include_children)
        elif isinstance(value, dict):
            return SpeckleObjectConverter._process_dict(value, depth, max_depth, include_children)
        else:
            return SpeckleObjectConverter.convert_to_dict(value, depth+1, max_depth, include_children)
    
    @staticmethod
    def _process_list(items: List, depth: int, max_depth: int, include_children: bool) -> List:
        """Process a list of items with truncation.
        
        Args:
            items: The list to process
            depth: Current recursion depth
            max_depth: Maximum recursion depth
            include_children: Whether to include all children objects
            
        Returns:
            Processed list
        """
        if not items:
            return []
            
        # Truncate list and process items
        limit = 5
        result = [SpeckleObjectConverter.convert_to_dict(item, depth+1, max_depth, include_children) 
                 for item in items[:limit]]
        
        # Add note about truncated items
        if len(items) > limit:
            result.append({"_note": f"...{len(items)-limit} more items"})
            
        return result
    
    @staticmethod
    def _process_dict(data: Dict, depth: int, max_depth: int, include_children: bool) -> Dict:
        """Process a dictionary with truncation.
        
        Args:
            data: The dictionary to process
            depth: Current recursion depth
            max_depth: Maximum recursion depth
            include_children: Whether to include all children objects
            
        Returns:
            Processed dictionary
        """
        if not data:
            return {}
            
        # Truncate dictionary and process items
        limit = 5
        result = {k: SpeckleObjectConverter.convert_to_dict(v, depth+1, max_depth, include_children) 
                 for k, v in list(data.items())[:limit]}
        
        # Add note about truncated items
        if len(data) > limit:
            result["_note"] = f"...{len(data)-limit} more items"
            
        return result
    
    @staticmethod
    def _process_dict_result(data: Dict, depth: int, max_depth: int, include_children: bool) -> Dict:
        """Process a dictionary result from to_dict() method.
        
        Args:
            data: The dictionary to process
            depth: Current recursion depth
            max_depth: Maximum recursion depth
            include_children: Whether to include all children objects
            
        Returns:
            Processed dictionary
        """
        # If we're at max depth and not including children, return a reference
        if depth > max_depth and include_children is False:
            return {"id": data.get("id"), "_type": "reference"}
            
        # Process each key in the dictionary
        result = {}
        for key, value in data.items():
            if key.startswith("_"):
                continue
                
            result[key] = SpeckleObjectConverter._process_value(value, depth, max_depth, include_children)
            
        return result
    
    @staticmethod
    def convert_value(value: Any) -> Any:
        """Convert a value to a serializable format.
        
        This is a simpler conversion method that doesn't limit recursion depth,
        suitable for converting specific properties.
        
        Args:
            value: The value to convert
            
        Returns:
            A serializable representation of the value
        """
        # Try to use the built-in to_dict method if available
        if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
            try:
                return value.to_dict()
            except Exception:
                pass  # Fall back to custom conversion if the built-in method fails
        
        if hasattr(value, '__dict__'):
            return {k: SpeckleObjectConverter.convert_value(v) for k, v in value.__dict__.items() if not k.startswith('_')}
        elif isinstance(value, list):
            return [SpeckleObjectConverter.convert_value(item) for item in value]
        elif isinstance(value, dict):
            return {k: SpeckleObjectConverter.convert_value(v) for k, v in value.items()}
        elif isinstance(value, (str, int, float, bool)) or value is None:
            return value
        else:
            return str(value)

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
async def list_projects(limit: int = 20) -> str:
    """List all projects accessible with the configured Speckle token.
    
    Args:
        limit: Maximum number of projects to retrieve (default: 20)
    """
    client = get_speckle_client()
    
    # Get the current user's projects
    logger.info(f"Retrieving user projects (limit: {limit})")
    projects_collection = client.active_user.get_projects(limit=limit)
    
    if not projects_collection or not projects_collection.items:
        logger.info("No projects found for the configured Speckle account")
        return "No projects found for the configured Speckle account."
    
    # Format project information
    project_list = []
    logger.info(f"Found {len(projects_collection.items)} projects")
    
    for project in projects_collection.items:
        # Build project info using a list instead of string concatenation
        info_parts = [
            f"ID: {project.id}",
            f"Name: {project.name}"
        ]
        
        if project.description:
            info_parts.append(f"Description: {project.description}")
        
        info_parts.extend([
            f"Visibility: {project.visibility.value}",
            f"Created: {format_datetime(project.created_at)}",
            f"Last Updated: {format_datetime(project.updated_at)}"
        ])
        
        # Join the parts with newlines
        project_list.append("\n".join(info_parts))
    
    return f"Found {len(project_list)} projects:\n\n" + "\n\n---\n\n".join(project_list)

@mcp.tool()
@handle_exceptions
async def get_project_details(project_id: str, limit: int = 20) -> str:
    """Get detailed information about a specific Speckle project.
    
    Args:
        project_id: The ID of the Speckle project to retrieve
        limit: Maximum number of models to retrieve (default: 20)
    """
    client = get_speckle_client()
    
    # Get the project details
    logger.info(f"Retrieving details for project: {project_id}")
    project = client.project.get(project_id)
    
    if not project:
        logger.warning(f"No project found with ID: {project_id}")
        return f"No project found with ID: {project_id}"
    
    # Get project models
    logger.info(f"Retrieving models for project: {project_id} (limit: {limit})")    
    project_with_models = client.project.get_with_models(project_id, models_limit=limit)
    models_count = project_with_models.models.total_count if project_with_models.models else 0
    
    # Get project team
    logger.info(f"Retrieving team for project: {project_id}")
    project_with_team = client.project.get_with_team(project_id)
    team_count = len(project_with_team.team) if project_with_team.team else 0
    
    # Format project details using a list instead of string concatenation
    details_parts = [
        f"Project: {project.name}",
        f"ID: {project.id}"
    ]
    
    if project.description:
        details_parts.append(f"Description: {project.description}")
    
    details_parts.extend([
        f"Visibility: {project.visibility.value}",
        f"Created: {format_datetime(project.created_at)}",
        f"Last Updated: {format_datetime(project.updated_at)}",
        f"Models: {models_count}",
        f"Team Members: {team_count}"
    ])
    
    if project.source_apps:
        details_parts.append(f"Source Applications: {', '.join(project.source_apps)}")
    
    # Add models if available
    if models_count > 0:
        details_parts.append("\nModels:")
        for model in project_with_models.models.items:
            details_parts.append(f"- {model.name} (ID: {model.id})")
    
    logger.info(f"Successfully retrieved details for project: {project_id}")
    return "\n".join(details_parts)

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
        # Build project info using a list instead of string concatenation
        info_parts = [
            f"ID: {project.id}",
            f"Name: {project.name}"
        ]
        
        if project.description:
            info_parts.append(f"Description: {project.description}")
        
        info_parts.append(f"Visibility: {project.visibility.value}")
        
        # Join the parts with newlines
        project_list.append("\n".join(info_parts))
    
    return f"Found {len(project_list)} projects matching '{query}':\n\n" + "\n\n---\n\n".join(project_list)

@mcp.tool()
@handle_exceptions
async def get_model_versions(project_id: str, model_id: str, limit: int = 20) -> str:
    """Get all versions for a specific model in a project.
    
    Args:
        project_id: The ID of the Speckle project
        model_id: The ID of the model to retrieve versions for
        limit: Maximum number of versions to retrieve (default: 20)
    """
    client = get_speckle_client()
    
    # Get versions for the specified model
    logger.info(f"Retrieving versions for model {model_id} in project {project_id} (limit: {limit})")
    versions = client.version.get_versions(model_id, project_id, limit=limit)
    
    if not versions or not versions.items:
        logger.info(f"No versions found for model {model_id} in project {project_id}")
        return f"No versions found for model {model_id} in project {project_id}."
    
    # Format versions information
    version_list = []
    logger.info(f"Found {len(versions.items)} versions for model {model_id}")
    
    for version in versions.items:
        # Build version info using a list instead of string concatenation
        info_parts = [
            f"Version ID: {version.id}",
            f"Message: {version.message or 'No message'}",
            f"Source Application: {version.source_application or 'Unknown'}",
            f"Created: {format_datetime(version.created_at, include_time=True)}",
            f"Referenced Object ID: {version.referenced_object}"
        ]
        
        if version.author_user:
            info_parts.append(f"Author: {version.author_user.name} ({version.author_user.id})")
        
        # Join the parts with newlines
        version_list.append("\n".join(info_parts))
    
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
    
    # Use the utility function to navigate through the object structure
    logger.info(f"Querying property path: {property_path}")
    property_value, error = get_property_by_path(speckle_object, property_path)
    
    if error:
        logger.warning(f"Error navigating property path: {error}")
        return f"Error: {error}"
    
    # Convert the result to a serializable format using the converter
    logger.info(f"Successfully retrieved property at path: {property_path}")
    result = {
        "property_path": property_path,
        "value": SpeckleObjectConverter.convert_value(property_value)
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
