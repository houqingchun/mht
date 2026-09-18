# Phase 1 认证接口

## 登录

`POST /api/v1/auth/login`

```json
{
  "role": "student",
  "account": "S001",
  "password": "123456"
}
```

成功后返回 `access_token`、`token_type` 和当前用户。响应不包含密码或密码哈希。

## 当前用户

`GET /api/v1/auth/me`

需要 `Authorization: Bearer <token>`。

## 管理员账号列表

`GET /api/v1/admin/accounts`

仅 `admin` 角色可访问。

## 管理员重置密码

`POST /api/v1/admin/accounts/{id}/reset-password`

```json
{
  "temporary_password": "654321",
  "purpose": "本地验收测试"
}
```

重置后旧密码失效，目标账号 `must_change_password=true`，并写入审计日志。

