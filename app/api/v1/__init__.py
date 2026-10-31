"""
API v1 package.

Assembles all v1 routers under the /api/v1 prefix so main.py
only needs to include a single router.

Adding a new v1 module in a future phase requires:
  1. Create app/api/v1/<module>_routes.py
  2. Import its router here and include it in v1_router
"""

from fastapi import APIRouter

from app.api.v1.health_routes import router as health_router
from app.api.v1.query_routes import router as query_router        # Phase 3
from app.api.v1.execution_routes import router as execution_router  # Phase 4B
from app.api.v1.explain_routes import router as explain_router      # Phase 4C
from app.api.v1.optimize_routes import router as optimize_router    # Phase 4D
from app.api.v1.performance_routes import router as performance_router  # Phase 4E
from app.api.v1.schema_routes import router as schema_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(health_router)
v1_router.include_router(schema_router)
v1_router.include_router(query_router)       # Phase 3: NL-to-SQL generation
v1_router.include_router(execution_router)   # Phase 4B: Safe SQL execution
v1_router.include_router(explain_router)     # Phase 4C: Query plan analysis
v1_router.include_router(optimize_router)    # Phase 4D: Query optimization
v1_router.include_router(performance_router) # Phase 4E: Performance analysis

__all__ = ["v1_router"]

