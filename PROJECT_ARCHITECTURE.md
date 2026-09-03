# PROJECT_ARCHITECTURE.md

# 面向耕地非粮化监管的主从多智能体决策平台

Project Architecture Specification

---

# 1. Design Philosophy

本项目采用 **职责优先（Responsibility First）** 的架构设计，而不是按照技术类型进行分类。

目录的存在是因为承担一种稳定职责，而不是因为使用了某种技术。

例如：

✔ Spatial Agent 是一种职责

✔ Knowledge Agent 是一种职责

✘ GeoPandas 不是目录

✘ Excel 不是目录

所有新增代码必须首先判断：

> **它承担什么职责？**

而不是：

> **它使用什么技术？**

---

# 2. Project Structure

backend/

├── api/
│
├── agent/
│ ├── master_agent/
│ ├── spatial_agent/
│ └── knowledge_agent/
│
├── skills/
│ ├── gis/
│ ├── rag/
│ └── report/
│
├── infrastructure/
│ ├── database/
│ ├── gis/
│ ├── llm/
│ └── vectorstore/
│
├── resources/
│ ├── prompts/
│ ├── templates/
│ ├── policies/
│ ├── cases/
│ └── examples/
│
├── utils/
│
│
└── main.py

---

# 3. Responsibility of Each Layer

## api/

职责：

- HTTP接口
- FastAPI Router
- 请求解析
- 返回结果

允许：

- routes
- schemas

原则：

API 只负责通信，不负责业务。

---

## agent/

系统核心。

论文中的所有研究对象都在这里。

模块之间按照 Agent 进行划分，而不是按照章节划分。

包括：

Master Agent

Spatial Agent

Knowledge Agent

每个 Agent 只负责自己的职责。

### agent/master_agent/

职责：

整个系统唯一调度中心。

负责：

- 用户意图理解
- 任务规划
- Agent调度
- 结果融合
- 最终监管决策

---

### agent/spatial_agent/

职责：

空间认知。

负责：

- GIS分析
- 空间统计
- 空间关系
- 地图分析

允许：

调用 GIS Skills。

禁止：

政策推理。

最终决策。

---

### agent/knowledge_agent/

职责：

监管知识理解。

负责：

- RAG
- 政策检索
- 法规解析
- 案例检索

允许：

调用 RAG Skills。

禁止：

GIS分析。

禁止：

最终监管决策。

---

# 4. Skills Layer

Skills 是 Agent 的能力封装。

一个 Skill 完成一个明确能力。

Skill 不负责业务。

Skill 可以被多个 Agent 共享。

例如：

GIS Skill

Buffer

Overlay
...

Skill 可以调用 Infrastructure。

Skill 不允许调用 Agent。

---

# 5. Infrastructure Layer

这里只允许出现技术实现。

---

# 6. Resources Layer

Resources 存放所有非代码资源。

包括：

Prompt

Few-shot

模板

案例

政策

所有 Prompt 必须放入：

resources/prompts/

所有政策文件：

resources/policies/

所有案例：

resources/cases/

所有模板：

resources/templates/

禁止：

Python代码。

---

# 7. Utils Layer

## 这里只允许真正的工具。

# 8. Calling Relationship

整个系统调用方向固定：

API

↓

Master Agent

↓

Spatial Agent
Knowledge Agent

↓

Skills

↓

Infrastructure

↓

Database / GIS / LLM / Vector Database

禁止反向调用。

Infrastructure 不允许调用 Agent。

Skill 不允许调用 Master Agent。

Agent 不允许调用 API。

---

# 11. Naming Convention

统一命名：

xxx_agent.py

xxx_skill.py

xxx_model.py

xxx_schema.py

xxx_prompt.md

xxx_template.md

xxx_case.json

xxx_policy.pdf

禁止：

test1.py

demo.py

new.py

temp.py

等无意义命名。

---

# 12. New Feature Development Workflow

新增功能时必须遵循以下流程：

Step 1

判断属于哪个 Agent。

↓

Step 2

判断是否已有对应 Skill。

↓

Step 3

若没有 Skill，则新增 Skill。

↓

Step 4

Skill 调用 Infrastructure。

↓

Step 5

Master Agent 统一调度。

禁止跨层实现业务。

---

# 13. Architecture Principles

原则一：

按职责分类，不按技术分类。

原则二：

Agent 负责思考。

Skill 负责能力。

Infrastructure 负责实现。

原则三：

一个模块只承担一种职责（Single Responsibility）。

原则四：

所有业务流程必须由 Master Agent 发起。

原则五：

所有 GIS 能力必须封装为 Skill。

原则六：

Prompt、模板、案例、政策全部放入 Resources，不与代码混放。

原则七：

新增代码前，必须先确定其职责归属，再决定目录位置。

不得为了方便而随意放入 utils 或其他目录。

---
