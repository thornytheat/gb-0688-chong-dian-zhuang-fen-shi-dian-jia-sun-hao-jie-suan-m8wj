# 运行维护

主服务端口与Compose保持一致。数据保存在records命名卷，应用代码不需要写入权限。

```sh
docker compose logs --tail=80 app
docker compose restart app
docker compose exec -T app pytest -q
docker compose down --remove-orphans
```

健康检查GET /health。失败输入返回原因码，结构性错误422，过大文件413，陈旧复核409。
文件内容最大4MB。批次先计算再原子保存，失败不留半条记录。证据CSV按原始行号定位。

## 月度需量

`GET /api/bills` 查看账单台账；`POST /api/m8` 才写入月度账单，浏览 `/m/8` 只是预览。`POST /api/bills/<月份>/<站点>` 复核当前版本，版本号落后返回409。补数失败或缺失超过全月10%时该站不出新账，已有账单保持原样可读。备份records卷即同时备份批次与月度账单台账。
