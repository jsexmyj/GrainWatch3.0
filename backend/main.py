import sys
import os

print("Starting GrainWatch API...")

from backend.utils.config import ConfigManager
from backend.utils.paths import PATHS
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# ======= 1. 导入路由 =======
# from app.api.routes.procedure_route import router as procedure_router

ConfigManager.load_config()

app = FastAPI(title="主从多智能体 API", version="1.0")

# ======= 跨域配置 =======
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发阶段全开放
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加这些配置到你的FastAPI应用
UPLOAD_DIR = PATHS.data  # 创建上传目录
PATHS.ensure_dir(PATHS.data)

# 添加静态文件服务，这样上传的文件可以通过URL访问
app.mount("/data", StaticFiles(directory=str(PATHS.data)), name="data")

# ======= 2. 注册接口路由 =======
# app.include_router(params_router)


# 错误异常处理
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )


# ======= 启动入口 =======
if __name__ == "__main__":
    uvicorn.run(app, port=8000)
