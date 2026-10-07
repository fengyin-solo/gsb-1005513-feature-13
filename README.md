# 光伏电站运维管理平台

面向光伏组件、逆变器、汇流箱、变压器、储能与升压站运行监视的集中式电站运维后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 光伏阵列 | `pv_array` | 光伏阵列 | 阵列编号、所属片区、组件型号 |
| 逆变器监视 | `inverter` | 逆变器 | 逆变器编号、品牌型号、额定功率 |
| 汇流箱检测 | `combiner_box` | 汇流箱 | 汇流箱编号、所属阵列、输入路数 |
| 变压器监视 | `transformer` | 变压器 | 变压器编号、电压等级、额定容量 |
| 储能电池组 | `energy_storage` | 储能电池组 | 电池组编号、电池类型、额定容量 |
| 升压站监视 | `boosting_station` | 升压站 | 升压站编号、进线电压、出线电压 |
| 关口计量 | `meter` | 关口表计 | 表计编号、计量点名称、表计精度 |
| 环境监测站 | `environment` | 环境监测站 | 站点编号、安装位置、辐照度 |
| 组件清洗 | `cleaning` | 清洗任务 | 任务编号、清洗区域、清洗方式 |
| 巡视检查 | `patrol` | 巡视记录 | 记录编号、巡视区域、巡视日期 |
| 缺陷管理 | `defect` | 设备缺陷 | 缺陷编号、发现日期、缺陷设备 |
| 检修计划 | `maintenance` | 检修计划 | 计划编号、检修设备、检修类别 |
| 备品备件 | `spare_parts` | 备件物料 | 备件编号、备件名称、规格型号 |
| 告警事件 | `alarm` | 告警事件 | 告警编号、告警来源、告警类型 |
| 调度指令 | `dispatch` | 调度指令单 | 指令编号、下发单位、指令类型 |
| 安全措施 | `safety` | 安全措施票 | 措施编号、措施类型、涉及设备 |
| 运维合同 | `contract` | 运维合同 | 合同编号、合同名称、签约甲方 |
| 运行月报 | `report` | 运行月报 | 月报编号、统计月份、发电量 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 运行月报

- 发电量、等效利用小时、综合效率PR、设备可利用率均由各模块明细自动汇总，
  公式只有一份：`backend/app/services/report_caliber.py`。
- 月报落 SQLite（`backend/data/report.db`，可用 `REPORT_DB_PATH` 覆盖）：
  重算结果整体覆盖写库，刷新或重启读到的就是最后一次重算结果。
- 口径版本化调整（`POST /api/report/calibers`）：发布后所有未归档月报自动按
  新口径重算；已归档月报冻结归档时的口径版本，归档详情与月报列表读同一份落库数据。
- 状态机：草稿 → 已复核 → 已归档。归档后复核/重算拒绝；已复核再次复核退回草稿；
  复核结论随归档快照写入归档台账（`report_archive`）。
- 按统计月份唯一：同月重复提交只留最新一版（版本号 +1、回草稿），已归档月份拒绝覆盖；
  存量月报首次启动时按统计月份回填，同月只留最新。

### 测试

```bash
cd backend && .venv/bin/python -m unittest discover -s tests
```
