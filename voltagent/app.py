"""Minimal FastAPI scaffolding for the VoltAgent integration (Appendix A).

This module can be expanded with the endpoints described in the appendix.
The current stub simply exposes a health check and a template for future
routes.
"""

from fastapi import FastAPI, HTTPException

app = FastAPI(title="VoltAgent API")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# TODO: add POST/GET endpoints per Appendix A once requirements are clarified.
# e.g. /scan, /trade, /metrics etc.


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
