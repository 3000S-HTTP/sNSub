# sNSub

اشتراک V2Ray خودکار از لیست‌های رایگان عمومی، به‌همراه **بهترین ۵۹** نود تست‌شده. [English](README.md)

## اشتراک

آدرس پایه: `https://raw.githubusercontent.com/3000S-HTTP/sNSub/main/output/`

- **★ بهترین ۵۹ (تست زنده):** `best59_base64.txt`
- همه نودها: `configs_base64.txt`
- فقط VLESS و VMess: `configs_vless_vmess_base64.txt`
- به تفکیک پروتکل: `vless` · `vmess` · `trojan` · `ss` · `ssr` · `hysteria2` (برای base64 از `_base64.txt` استفاده کنید)

آینه CDN: `https://cdn.jsdelivr.net/gh/3000S-HTTP/sNSub@main/output/best59_base64.txt`

## نحوه کار

- اکشن گیت‌هاب هر ۶ ساعت اجرا می‌شود: `scripts/build.py` فایل [`sources.txt`](sources.txt) را می‌خواند، ادغام و حذف تکراری انجام می‌دهد.
- `scripts/tester.py` نودها را با **Xray-core** تست زنده می‌کند و **۵۹ نود سریع و سالم** را نگه می‌دارد → `output/best59.*`.
- لیست کامل هم منتشر می‌شود. برای افزودن/حذف منبع، `sources.txt` را ویرایش کنید (منبعی که اول باشد، اول تست می‌شود).

## اجرای محلی

```bash
python scripts/build.py
python scripts/tester.py --best 59
```

بر اساس [patterniha/Free-Configs](https://github.com/patterniha/Free-Configs) و [barry-far/v2ray-config](https://github.com/barry-far/v2ray-config) و [ebrasha/free-v2ray-public-list](https://github.com/ebrasha/free-v2ray-public-list).

نودهای رایگان عمومی مورد اعتماد نیستند — هرگز ترافیک حساس را از آن‌ها عبور ندهید. پروتکل‌های `hysteria2`/`tuic`/`ssr` تست زنده نمی‌شوند.