from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import json

import speckle_server as server

app = FastAPI(title="Speckle MCP HTTP Wrapper")

# Serve plugin files
@app.get("/openapi.yaml", include_in_schema=False)
async def openapi_spec():
    return FileResponse("openapi.yaml", media_type="text/yaml")

@app.get("/.well-known/ai-plugin.json", include_in_schema=False)
async def plugin_manifest():
    return FileResponse("ai-plugin.json", media_type="application/json")

# Helper to parse JSON if possible
def maybe_json(result: str) -> Response:
    try:
        data = json.loads(result)
        return JSONResponse(data)
    except Exception:
        return PlainTextResponse(result)

@app.get("/projects")
async def http_list_projects(limit: int = 20, cursor: str | None = None):
    result = await server.list_projects(limit, cursor)
    return maybe_json(result)

@app.get("/projects/{project_id}")
async def http_get_project_details(project_id: str, limit: int = 20):
    """HTTP endpoint to retrieve detailed information for a project."""
    result = await server.get_project_details(project_id, limit)
    return maybe_json(result)

@app.get("/projects/search")
async def http_search_projects(query: str, cursor: str | None = None):
    result = await server.search_projects(query, cursor)
    return maybe_json(result)

@app.get("/projects/{project_id}/models/{model_id}/versions")
async def http_get_model_versions(
    project_id: str,
    model_id: str,
    limit: int = 20,
    cursor: str | None = None,
):
    result = await server.get_model_versions(project_id, model_id, limit, cursor)
    return maybe_json(result)

@app.get("/projects/{project_id}/versions/{version_id}/objects")
async def http_get_version_objects(
    project_id: str,
    version_id: str,
    include_children: bool = False,
    cursor: str | None = None,
):
    result = await server.get_version_objects(project_id, version_id, include_children, cursor)
    return maybe_json(result)

@app.get("/projects/{project_id}/versions/{version_id}/query")
async def http_query_object_properties(project_id: str, version_id: str, property_path: str):
    result = await server.query_object_properties(project_id, version_id, property_path)
    return maybe_json(result)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
