.PHONY: install dev backend frontend test e2e docker-up docker-down docker-logs clean reset seed seed-demo purge-demo deploy-package

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

# 打 Windows 一键安装包 -> dist/心晴部署包.zip
#
# **在开发机（macOS / Linux）上跑，不是在目标机上跑。** 它要下载 Windows 版的
# CPython 与 win_amd64 的 wheel，并重新构建前端；目标机那边只解压、只双击。
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
