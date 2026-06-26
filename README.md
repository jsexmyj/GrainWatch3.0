# GrainWatch3.0

## 总体架构

```
backend/
│
├── api/                  ← 接口层（薄）
│   ├── routes/
│   ├── schemas/  只放HTTP协议中的参数定义，这叫schemas
│
├── modules/                # 系统核心模块（论文主体）
│   ├── master_agent/
│   ├── spatial_agent/
│   ├── knowledge_agent/
│
├── skills/                 # Skills能力封装
│
├── resources/              # 资源管理
│
├── infrastructure/         # 技术实现
│   ├── database/
│   ├── gis/
│   ├── rag/
│   ├── llm/
├── utils/               ← 通用能力
└── main.py
```

分类原则：
① 这是“领域逻辑”吗？
→ domain

② 这是“流程编排”吗？
→ application

③ 这是“技术实现”吗？
→ infrastructure
