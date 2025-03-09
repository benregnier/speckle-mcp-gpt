from typing import Any, List, Dict, Optional
import os
from mcp.server.fastmcp import FastMCP

# Import Speckle modules
from specklepy.api.client import SpeckleClient
from specklepy.api import operations
from specklepy.transports.server import ServerTransport
import json
from threading import Lock

# Initialize FastMCP server
mcp = FastMCP("speckle")

# Global variables
speckle_token = os.environ.get("SPECKLE_TOKEN", "")
speckle_server_url = os.environ.get("SPECKLE_SERVER", "https://app.speckle.systems")

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
    """
    try:
        return SpeckleClientSingleton.get_instance()
    except Exception as e:
        # If there's an authentication error, try refreshing the client
        if "authentication" in str(e).lower() or "token" in str(e).lower():
            return SpeckleClientSingleton.refresh_instance()
        raise

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
        
        # Use the built-in search_projects functionality of SpeckleClient
        # which is more efficient than retrieving all projects and filtering manually
        from specklepy.core.api.inputs.user_inputs import UserProjectsFilter
        
        # Create a filter with the search term
        filter = UserProjectsFilter(search=query)
        
        # Get projects using the filter
        projects_collection = client.active_user.get_projects(filter=filter)
        
        if not projects_collection or not projects_collection.items:
            return f"No projects found matching the search term: '{query}'"
        
        # Format project information
        project_list = []
        for project in projects_collection.items:
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

@mcp.tool()
async def get_version_objects(project_id: str, version_id: str, include_children: bool = False) -> str:
    """Get objects from a specific version in a project.
    
    Args:
        project_id: The ID of the Speckle project
        version_id: The ID of the version to retrieve objects from
        include_children: Whether to include children objects in the response
    """
    try:
        client = get_speckle_client()
        
        # Get the version to access its referenced object ID
        version = client.version.get(version_id, project_id)
        
        if not version:
            return f"Version {version_id} not found in project {project_id}."
        
        # Get the referenced object ID
        object_id = version.referenced_object
        
        # Create a server transport to receive the object
        transport = ServerTransport(project_id, client)
        
        # Receive the object
        speckle_object = operations.receive(object_id, transport)
        
        # Convert the object to a dictionary
        def convert_to_dict(obj, depth=0, max_depth=2):
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
                            result[key] = [convert_to_dict(item, depth+1, max_depth) for item in value[:5]]
                            if len(value) > 5:
                                result[key].append({"_note": f"...{len(value)-5} more items"})
                        else:
                            result[key] = []
                    elif isinstance(value, dict):
                        result[key] = {k: convert_to_dict(v, depth+1, max_depth) for k, v in list(value.items())[:5]}
                        if len(value) > 5:
                            result[key]["_note"] = f"...{len(value)-5} more items"
                    else:
                        result[key] = convert_to_dict(value, depth+1, max_depth)
                return result
            elif isinstance(obj, (str, int, float, bool)) or obj is None:
                return obj
            elif isinstance(obj, list):
                return [convert_to_dict(item, depth+1, max_depth) for item in obj[:5]]
            elif isinstance(obj, dict):
                return {k: convert_to_dict(v, depth+1, max_depth) for k, v in list(obj.items())[:5]}
            return str(obj)
        
        # Convert the Speckle object to a serializable dictionary
        obj_dict = convert_to_dict(speckle_object)
        
        # Return basic info and structured data
        result = {
            "version_id": version_id,
            "object_id": object_id,
            "created_at": version.created_at.isoformat(),
            "data": obj_dict
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        import traceback
        return f"Error retrieving version objects: {str(e)}\n{traceback.format_exc()}"

@mcp.tool()
async def query_object_properties(project_id: str, version_id: str, property_path: str) -> str:
    """Query specific properties from objects in a version.
    
    Args:
        project_id: The ID of the Speckle project
        version_id: The ID of the version to retrieve objects from
        property_path: The dot-notation path to the property (e.g., "elements.0.name")
    """
    try:
        client = get_speckle_client()
        
        # Get the version to access its referenced object ID
        version = client.version.get(version_id, project_id)
        
        if not version:
            return f"Version {version_id} not found in project {project_id}."
        
        # Get the referenced object ID
        object_id = version.referenced_object
        
        # Create a server transport to receive the object
        transport = ServerTransport(project_id, client)
        
        # Receive the object
        speckle_object = operations.receive(object_id, transport)
        
        # Parse the property path
        path_parts = property_path.split('.')
        
        # Navigate through the object structure
        current = speckle_object
        path_so_far = ""
        
        for i, part in enumerate(path_parts):
            path_so_far += ("" if i == 0 else ".") + part
            
            # Handle array indices
            if part.isdigit() and isinstance(current, list):
                index = int(part)
                if index < len(current):
                    current = current[index]
                else:
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
                return f"Error: Property '{part}' not found at path '{path_so_far}'"
        
        # Convert the result to a serializable format
        def convert_value(val):
            if hasattr(val, '__dict__'):
                return {k: convert_value(v) for k, v in val.__dict__.items() if not k.startswith('_')}
            elif isinstance(val, list):
                return [convert_value(item) for item in val]
            elif isinstance(val, dict):
                return {k: convert_value(v) for k, v in val.items()}
            elif isinstance(val, (str, int, float, bool)) or val is None:
                return val
            else:
                return str(val)
        
        result = {
            "property_path": property_path,
            "value": convert_value(current)
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        import traceback
        return f"Error querying object properties: {str(e)}\n{traceback.format_exc()}"

@mcp.tool()
async def extract_geometry_data(project_id: str, version_id: str, geometry_type: str = "all") -> str:
    """Extract geometry data from a version for 3D visualization.
    
    Args:
        project_id: The ID of the Speckle project
        version_id: The ID of the version to retrieve geometry from
        geometry_type: The type of geometry to extract ("all", "mesh", "point", "line", "curve")
    """
    try:
        client = get_speckle_client()
        
        # Get the version to access its referenced object ID
        version = client.version.get(version_id, project_id)
        
        if not version:
            return f"Version {version_id} not found in project {project_id}."
        
        # Get the referenced object ID
        object_id = version.referenced_object
        
        # Create a server transport to receive the object
        transport = ServerTransport(project_id, client)
        
        # Receive the object
        speckle_object = operations.receive(object_id, transport)
        
        # Function to recursively find all geometry objects of specified type
        def find_geometry(obj, geom_type="all", found=None, path="", max_depth=10, current_depth=0):
            if found is None:
                found = []
            
            # Prevent stack overflow with depth limit
            if current_depth > max_depth:
                return found
            
            # Check if it's a geometry object we're looking for
            is_target = False
            obj_type = getattr(obj, "speckle_type", "")
            
            if geom_type == "all" and "Geometry" in obj_type:
                is_target = True
            elif geom_type.lower() == "mesh" and "Mesh" in obj_type:
                is_target = True
            elif geom_type.lower() == "point" and "Point" in obj_type:
                is_target = True
            elif geom_type.lower() == "line" and "Line" in obj_type:
                is_target = True
            elif geom_type.lower() == "curve" and any(t in obj_type for t in ["Curve", "Arc", "Circle", "Ellipse"]):
                is_target = True
            
            if is_target:
                # Extract core geometry data
                geom_data = {"type": obj_type, "path": path}
                
                # Extract type-specific properties
                if "Mesh" in obj_type and hasattr(obj, "vertices") and hasattr(obj, "faces"):
                    geom_data["vertices"] = obj.vertices[:100] if len(obj.vertices) > 100 else obj.vertices
                    geom_data["faces"] = obj.faces[:100] if len(obj.faces) > 100 else obj.faces
                    geom_data["vertices_truncated"] = len(obj.vertices) > 100
                    geom_data["faces_truncated"] = len(obj.faces) > 100
                    geom_data["vertex_count"] = len(obj.vertices) // 3 if hasattr(obj, "vertices_count") else len(obj.vertices) // 3
                
                elif "Point" in obj_type and hasattr(obj, "x") and hasattr(obj, "y") and hasattr(obj, "z"):
                    geom_data["x"] = obj.x
                    geom_data["y"] = obj.y
                    geom_data["z"] = obj.z
                
                elif "Line" in obj_type and hasattr(obj, "start") and hasattr(obj, "end"):
                    if hasattr(obj.start, "x"):
                        geom_data["start"] = {"x": obj.start.x, "y": obj.start.y, "z": obj.start.z}
                    if hasattr(obj.end, "x"):
                        geom_data["end"] = {"x": obj.end.x, "y": obj.end.y, "z": obj.end.z}
                
                # Extract units if available
                if hasattr(obj, "units"):
                    geom_data["units"] = obj.units
                
                found.append(geom_data)
            
            # Traverse child objects
            if hasattr(obj, "__dict__"):
                for key, value in obj.__dict__.items():
                    if key.startswith("_"):
                        continue
                    
                    new_path = f"{path}.{key}" if path else key
                    
                    if isinstance(value, list):
                        # Limit the number of items to process to prevent excessive recursion
                        for i, item in enumerate(value[:100]):  # Process max 100 items
                            if hasattr(item, "__dict__"):
                                find_geometry(item, geom_type, found, f"{new_path}.{i}", max_depth, current_depth + 1)
                    elif hasattr(value, "__dict__"):
                        find_geometry(value, geom_type, found, new_path, max_depth, current_depth + 1)
            
            return found
        
        # Find all relevant geometry objects with depth limit to prevent stack overflow
        geometry_objects = find_geometry(speckle_object, geometry_type)
        
        # Limit result size to prevent excessive responses
        result_objects = geometry_objects[:100]
        truncated = len(geometry_objects) > 100
        
        result = {
            "version_id": version_id,
            "object_id": object_id,
            "geometry_type": geometry_type,
            "total_found": len(geometry_objects),
            "truncated": truncated,
            "geometry": result_objects
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        import traceback
        return f"Error extracting geometry data: {str(e)}\n{traceback.format_exc()}"

if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
