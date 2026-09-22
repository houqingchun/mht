.PHONY: install dev backend frontend test e2e docker-up docker-down docker-logs clean reset seed seed-demo purge-demo db-upgrade-sql db-seed-sql deploy-package

# Install dependencies
install:
	cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[test]"
	cd frontend && npm install

# Start dev servers
dev:
	@echo "Starting backend on http://127.0.0.1:8000 ..."
	@cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
	@echo "Starting frontend on http://localhost:5173 ..."
	@cd frontend && npm run dev &
	@echo "Frontend: http://localhost:5173"
	@echo "API docs: http://127.0.0.1:8000/docs"
	@wait

backend:
	cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm run dev

# Run tests
test:
	cd backend && source .venv/bin/activate && pytest -v

e2e:
	npx playwright test

# Docker
docker-up:
	docker compose up -d

docker-down:
	docker compose down -v

docker-logs:
	docker compose logs -f

# Database
migrate:
	cd backend && source .venv/bin/activate && alembic upgrade head

seed:
	cd backend && source .venv/bin/activate && python -m app.db.seed

# 填入演示数据：多个年级班级、一批学生、覆盖各分数段的已提交测评、
# 以及处在关怀闭环各个阶段的档案。可重复执行。
seed-demo:
	cd backend && source .venv/bin/activate && python -m app.db.seed_demo

reset-db:
	cd backend && source .venv/bin/activate && python -m app.db.purge reset

# 清掉 seed-demo 填进去的一切（演示学生/账号/年级班级、示范测评与档案、可追溯到它们的
# 审计行），只留 seed.py 的基线，然后把基线任务种回来。可重复执行。
# 仅用于开发库：库里没有标记演示数据的列，名册由 seed_demo.demo_roster() 定义，
# 学号落在生成区间里的真实学生会被一起删掉。
purge-demo:
	cd backend && source .venv/bin/activate && python -m app.db.purge demo

# 重新生成「从 V1.0.0 升到当前版本」的数据库增量 SQL
# -> backend/sql/upgrade_from_v1_0_0.sql 与 dist/ 下同一份。
#
# **改了 alembic 迁移就要重跑它。** 产物是从两份既有来源现渲染出来的（链上每条迁移的
# PRECHECKS 常量 + `alembic upgrade <基线>:head --sql`），所以迁移一动、不重跑，
# 仓库里那份快照与磁盘上那份文件就各说各话——而它会随 `backend/` 一起打进安装包，
# 落在客户手上。`app/tests/test_incremental_upgrade_sql.py` 盯着这条。
#
# 只写文件、不碰数据库，所以在开发机上跑是安全的。
db-upgrade-sql:
	cd backend && source .venv/bin/activate && python ../deploy/build_migration_sql.py

# 重新渲染 backend/sql/seed_mysql8.sql（+ dist/ 一份）——「只有系统基础数据与管理员账号」
# 的那份 DML 脚本。
#
# **它要连上一台活着的 MySQL**（与 db-upgrade-sql 不同，那一份是纯文件操作）：这份文件的
# 数据段必须来自 `seed.py` 的**结果**，不是它**代码**的副本——行里的 id 引用是 `db.flush()`
# 的产物，而在 SQL 里再抄一份基线必然漂移（CLAUDE.md §16）。所以它真的跑一遍
# `<主库名>_init` 这个一次性库：建库 → 迁移 → seed → reset_to_baseline → 读结果 → 删库。
#
# 它**只**碰 `<主库名>_init`（生成器里有三条断言把着：以 `_init` 结尾、与主库不同名、是
# mysql），**不碰开发库本身**。改过 `seed.py` / `data/mht_scale.json` / `reset_to_baseline.sql`
# 就要跑它，然后把 `backend/sql/seed_mysql8.sql` 一起提交——`test_seed_sql.py` 逐字节盯着。
#
# **不在 `make deploy-package` 里重新生成**（与 upgrade_from_v1_0_0.sql 相反）：那一份的
# 两个来源都长在当前源码树上，出包时源码树就是最新的；这一份多了一个**外部来源**（一个库），
# 出包时重生成反而会**盖掉**「有人改了 seed 却没重跑这里」这个信号。
db-seed-sql:
	cd backend && source .venv/bin/activate && python ../deploy/build_seed_sql.py

# 打 Windows 一键安装包 -> dist/心晴部署包_V<版本>.zip
#
# **在开发机（macOS / Linux）上跑，不是在目标机上跑。** 它要下载 win_amd64 的 wheel
# 并重新构建前端；目标机那边只解压、只双击（Python 3.11 与 MySQL 得先装好，见 §18）。
# 用它自己的 venv 跑：这个脚本要 import `packaging`（随 [test] extra 一起装）。
#
# 出完包请照 `deploy/README.md` 里那张清单在 Windows 上真装一次——
# 自检能验的东西有限（`.pyd` 能不能 import、schtasks 收不收，只有真机知道）。
deploy-package:
	cd backend && source .venv/bin/activate && python ../deploy/build_package.py

# Clean
clean:
	rm -rf backend/__pycache__ backend/app/__pycache__ backend/app/*/__pycache__
	rm -rf backend/.pytest_cache backend/.coverage
	rm -rf frontend/dist frontend/.vite
	rm -rf test-results playwright-report
	find frontend/src -name '*.vue.js' -delete
