"""Memory management routes using the same commands as conversational tools."""

from pydantic import ValidationError
from starlette.responses import JSONResponse
from starlette.routing import Route

from training_ai.memory_models import ForgetMemory, MemoryChange, MemoryConflict, MissingMemory


def memory_routes(store):
    async def listing(request):
        try:
            params = request.query_params
            return JSONResponse(
                store.list(
                    topic=params.get("topic", ""),
                    kind=params.get("kind", ""),
                    status=params.get("status", ""),
                    start=params.get("start"),
                    end=params.get("end"),
                    offset=int(params.get("offset", 0)),
                    limit=int(params.get("limit", 30)),
                )
            )
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid memory filters or pagination."}, 400)

    async def entry(request):
        entry_id = request.path_params["entry_id"]
        try:
            if request.method == "GET":
                return JSONResponse(store.get(entry_id))
            body = await request.json()
            if request.method == "PATCH":
                change = MemoryChange.model_validate(body)
                receipt = store.change(entry_id, change.expected_revision, change.data)
                return JSONResponse(
                    receipt["entry"] if receipt.get("entry") else store.get(entry_id)
                )
            deletion = ForgetMemory.model_validate(body)
            return JSONResponse(store.forget(entry_id, deletion.expected_revision))
        except MissingMemory:
            return JSONResponse({"error": "Memory entry not found."}, 404)
        except MemoryConflict as exc:
            return JSONResponse({"error": str(exc)}, 409)
        except (ValidationError, ValueError, UnicodeDecodeError):
            return JSONResponse(
                {"error": "Invalid memory data. Check dates, lifecycle and revision."}, 400
            )

    return [
        Route("/api/memory", listing),
        Route("/api/memory/{entry_id}", entry, methods=["GET", "PATCH", "DELETE"]),
    ]
