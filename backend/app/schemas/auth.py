from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    role: str = Field(pattern="^(student|counselor|leader|admin)$")
    account: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class ResetPasswordRequest(BaseModel):
    """管理员重置别人的密码。

    三个字段都是必填，而且**密码进得来、出不去**（§16.5「不返回或记录明文密码」）。
    此前 `temporary_password` 是可选的，服务端在它缺失时用 `token_urlsafe(9)` 造一个
    并回传——那条路必须回传，否则没人知道临时密码是什么，所以它与规格直接冲突。
    查过全部调用方（界面表单、安装脚本、测试）都显式传值，于是把这一半删掉不改动
    任何既有行为，只是让「不给密码就别调这个接口」变成一条能报的错。

    `purpose` 在这一版是**规格里的「原因」**（§16.5「管理员重置密码必须填写原因」）。
    名字沿用既有的 `purpose`，与其余审计入口一致——审计表上那一列就叫 `purpose`。
    """

    temporary_password: str = Field(min_length=6, max_length=128)
    purpose: str = Field(min_length=1, max_length=255)


class AccountScopeInput(BaseModel):
    """一条数据范围选择：类型 + 那个类型下的一行 id。

    形状与 `GET /admin/accounts/scope-options` 发出去的一致，所以界面把选中的
    那一个选项原样发回来即可，不需要在中间做一次翻译。

    类型的定义域**不在这里**收窄：不认识的类型要给一句中文（「可选：全校 / 年级 /
    班级」），而 pydantic 的 pattern 只会给一句英文的 string_pattern_mismatch。
    与 `PermissionEntry` 对能力键的处理同一条理由。
    """

    scope_type: str = Field(min_length=1, max_length=32)
    scope_id: int


class CreateAccountRequest(BaseModel):
    # 角色也不在这里收窄定义域，理由同上：`student` 要被**明确拒绝并说明为什么**
    # （学生账号跟着名册导入一起生成），而不是撞一句英文的 pattern 报错。
    role_code: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=128)
    account: str = Field(min_length=1, max_length=128)
    # 与「重置密码」同一个下限。这里有默认值可循（操作员自己填），所以**必填**——
    # 一个没有密码的账号没有意义，而服务端生成的那一支属于重置密码那条路径。
    temporary_password: str = Field(min_length=6, max_length=128)
    # 至少一条：没有范围的员工账号登录得进来却看不到任何学生（§9 的 fail-closed）。
    scopes: list[AccountScopeInput] = Field(min_length=1)


class UpdateAccountRequest(BaseModel):
    """改姓名 / 换范围 / 停用启用。三样都可以单独发。

    角色与账号不在其中，理由写在 `auth_service.update_account` 的 docstring 里。
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    scopes: list[AccountScopeInput] | None = Field(default=None, min_length=1)
    active: bool | None = None


class PermissionEntry(BaseModel):
    # 角色的定义域在这里收窄（和 LoginRequest 同一张名单）。能力和等级的定义域
    # 不在这里：那两张表住在 `security/permissions.py`，是能力的定义本身，而且
    # 拒绝时必须说清「这项能力接受哪些等级」——写在这里只会得到一句英文的
    # string_pattern_mismatch，操作员看不懂自己错在哪一格。
    role_code: str = Field(pattern="^(student|counselor|leader|admin)$")
    capability_key: str = Field(min_length=1, max_length=64)
    scope_level: str = Field(min_length=1, max_length=64)


class PermissionMatrixRequest(BaseModel):
    entries: list[PermissionEntry] = Field(min_length=1)

