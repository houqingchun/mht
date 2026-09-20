from pydantic import BaseModel


class AssessmentImportCommitRequest(BaseModel):
    """提交一个导入批次。

    **只有处置方式，没有别的**：批次名称、测评日期、文件指纹、逐行结论与答案都
    已经在预览那一步落进库了（`assessment_import_batch` / `assessment_import_row` /
    `assessment_external_result`），提交只需要说「那一批」——而那一批由**路径里的
    batch_id** 指认。V1.0 那个 `preview_token`（把整份预览签成一个 JWT）整个去掉了：
    令牌是死的，操作员没法在有问题的行上做任何事，唯一出路是重传一次文件；
    而重传之后拿到的是同一个批次（同操作者 + 同指纹 + 仍 `PREVIEW` 的会被复用），
    所以令牌能表达的东西，批次 id 一样表达得了，而且它还能被查。

    **处置方式只在批次里真的有需要拍板的行时必需**（`overwrite` / `skip`）。
    缺了它而批次里有那种行时，服务回 422 且**任何写入之前**——§18.6 的原话是
    「任何未处理的冲突不得进入正式结果」。

    用 `str` 而不是 `Literal[...]`：取值错要回统一响应封装里的那句中文，
    而 `Literal` 会被 FastAPI 自己的校验拦下，前端拿到的是框架的 422 结构，
    `api.ts` 取不到 `error.message`，只能显示一句笼统的失败（同 `parse_tested_on`
    不写成 `date` 的理由）。合法性在服务里判。
    """

    resolution: str | None = None
    # **第二个问题，与 `resolution` 并列**（§18.5）：`resolution` 回答「冲突的那些行
    # 写不写」，这一个回答「写下去时名册上那个年龄动不动、这一场按哪个年龄记」。
    # 取值 `keep_roster` / `overwrite` / `session_only`，合法性在服务里判（同 `resolution`
    # 不用 `Literal` 的理由：取值错要回统一封装里的那句中文）。
    age_resolution: str | None = None


class AssessmentImportRowResolveRequest(BaseModel):
    """逐行处置一条导入行（§18.6 的「逐行处理」）。

    三个字段都可以不传，而**都不传时这个请求什么也不改**（除了记下「有人看过这一行」
    的 `resolved_by` / `resolved_at`）——那是有用的：一行年龄不符的记录，操作员可能
    只想把它标记成「看过了，照整批的选择走」，而整批那一次还没发生。

    三项各自只在它问得着的行上有意义（`resolution` 对「要拍板」的那几档、
    `age_resolution` 只对有年龄冲突的行、`conflict_resolution` 只对与在线答卷冲突的行），
    服务对用不上的组合回 422 而不是静默忽略：静默忽略会在记录里留下一条谁也解释不了
    的决定。

    **`conflict_resolution` 是唯一没有整批版本的那一项**（§18.8，2026-09-19 用户裁决）：
    另外两项在 `AssessmentImportCommitRequest` 上都有对应字段，这一项**故意没有**
    ——一次点击同时替几个学生回答「要不要让外部那份顶掉他自己答的那一份」，正是这条
    路上唯一不能发生的事。所以它只在这里。
    """

    resolution: str | None = None
    age_resolution: str | None = None
    # 取值是 §18.8 的四档（`KEEP_ONLINE` / `USE_EXTERNAL` / `REJECT_EXTERNAL` /
    # `KEEP_BOTH_BUT_ONE_EFFECTIVE`）。用 `str` 而不是 `Literal`，与上面两项同一条：
    # 取值错要回统一封装里的中文（`CONFLICT_RESOLUTION_HINT`），而 pydantic 自己
    # 抛出来的那一条是英文的 422，与全站其余地方的措辞对不上。
    conflict_resolution: str | None = None
