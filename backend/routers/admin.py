import os

from fastapi import APIRouter, HTTPException, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse

from database import (
    get_all_tenants, get_tenant_by_id, get_tenant_stats,
    get_admin_by_username, get_admin_by_id, create_admin,
    update_tenant, create_tenant, slug_exists,
)
from auth import (
    hash_password, verify_password, create_token, decode_token,
    get_current_admin, require_root_admin,
)

admin_router = APIRouter()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend")


@admin_router.get("/panel/admin")
async def admin_page():
    return HTMLResponse(status_code=302, headers={"Location": "/admin-login"})


@admin_router.get("/panel/admin/dashboard")
async def admin_dashboard_page():
    path = os.path.join(TEMPLATE_DIR, "admin-dashboard.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Root Dashboard — Unsr!fess</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0e0e12;color:#e7e9ea;padding:20px}
.container{max-width:1200px;margin:0 auto}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid #2a2a38}
h1{font-size:1.5rem}
h2{font-size:1.2rem;margin-bottom:12px}
.logout-btn{padding:8px 20px;background:transparent;border:1px solid #2a2a38;border-radius:9999px;color:#e7e9ea;cursor:pointer}
.logout-btn:hover{border-color:#f4212e;color:#f4212e}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}
.stat-card{background:#1a1a24;border:1px solid #2a2a38;border-radius:12px;padding:16px;text-align:center}
.stat-card .num{font-size:1.8rem;font-weight:700;color:#1d9bf0}
.stat-card .label{font-size:.8rem;color:#71767b;margin-top:4px}
.tenants-table{background:#1a1a24;border:1px solid #2a2a38;border-radius:12px;overflow:hidden}
.tenants-table table{width:100%;border-collapse:collapse}
.tenants-table th,.tenants-table td{padding:12px 16px;text-align:left;border-bottom:1px solid #2a2a38}
.tenants-table th{background:#0e0e12;font-size:.8rem;color:#71767b;text-transform:uppercase}
.tenants-table td{font-size:.9rem}
.badge{padding:2px 8px;border-radius:9999px;font-size:.75rem}
.badge.active{background:#00ba7c22;color:#00ba7c;border:1px solid #00ba7c}
.badge.inactive{background:#f4212e22;color:#f4212e;border:1px solid #f4212e}
.actions{display:flex;gap:6px}
.actions button{padding:4px 12px;border-radius:6px;border:none;font-size:.8rem;cursor:pointer}
.btn-activate{background:#00ba7c;color:#fff}
.btn-deactivate{background:#f4212e;color:#fff}
.btn-login-as{background:#1d9bf0;color:#fff}
</style>
</head>
<body>
<div class="container">
<div class="header"><h1>Root Dashboard</h1><button class="logout-btn" onclick="logout()">Sign Out</button></div>
<div class="stats-grid" id="stats"></div>
<div class="tenants-table" id="tenantsContainer"><table><thead><tr><th>ID</th><th>Name</th><th>Slug</th><th>X Handle</th><th>Status</th><th>Tweets</th><th>Admins</th><th>Created</th><th>Actions</th></tr></thead><tbody id="tenantsBody"></tbody></table></div>
</div>
<script>
function getToken(){return localStorage.getItem('admin_token')}
function logout(){localStorage.removeItem('admin_token');localStorage.removeItem('admin_role');window.location.href='/admin-login'}
async function api(url,opts={}){
const t=getToken();if(!t){window.location.href='/admin-login';return}
opts.headers=opts.headers||{};opts.headers['Authorization']='Bearer '+t
const resp=await fetch(url,opts);if(resp.status===401){logout();return}
return resp.json()
}
async function loadStats(){
const stats=await api('/panel/api/admin/stats');if(!stats)return
document.getElementById('stats').innerHTML=Object.entries(stats).map(([k,v])=>`<div class="stat-card"><div class="num">${v}</div><div class="label">${k.replace(/_/g,' ')}</div></div>`).join('')
}
async function loadTenants(){
const tenants=await api('/panel/api/admin/tenants');if(!tenants)return
document.getElementById('tenantsBody').innerHTML=tenants.map(t=>{
const badge=t.is_active?'<span class="badge active">Active</span>':'<span class="badge inactive">Inactive</span>'
const created=new Date(t.created_at).toLocaleDateString()
return `<tr><td>${t.id}</td><td>${t.name}</td><td>${t.slug}</td><td>@${t.x_screen_name||'-'}</td><td>${badge}</td><td>${t.total_tweets||0}</td><td>${t.total_admins||0}</td><td>${created}</td>
<td class="actions">${t.is_active?`<button class="btn-deactivate" onclick="toggleTenant(${t.id},false)">Deactivate</button>`:`<button class="btn-activate" onclick="toggleTenant(${t.id},true)">Activate</button>`}
<button class="btn-login-as" onclick="loginAs(${t.id})">Login As</button></td></tr>`
}).join('')
}
async function toggleTenant(id,activate){
await api('/panel/api/admin/tenants/'+id+'/'+(activate?'activate':'deactivate'),{method:'POST'})
loadTenants()
}
async function loginAs(id){
const data=await api('/panel/api/admin/tenants/'+id+'/login-as',{method:'POST'})
if(data&&data.token){localStorage.setItem('admin_token',data.token);window.location.href='/'+data.slug+'/panel/dashboard'}
}
loadStats();loadTenants()
</script>
</body>
</html>""")


@admin_router.post("/panel/api/admin/login")
async def admin_login(username: str = Form(...), password: str = Form(...)):
    admin = await get_admin_by_username(username, tenant_id=None)
    if not admin:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not admin.get("is_root"):
        raise HTTPException(status_code=401, detail="Not a root admin account")
    if not admin["is_active"]:
        raise HTTPException(status_code=401, detail="Account deactivated")
    if not verify_password(password, admin["password"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_token(admin["id"], admin["role"], tenant_id=None, is_root=True)
    return {"token": token, "role": admin["role"], "display_name": admin["display_name"]}


@admin_router.get("/panel/api/admin/me")
async def admin_me(admin: dict = Depends(require_root_admin)):
    row = await get_admin_by_id(admin["id"])
    if not row:
        raise HTTPException(status_code=404, detail="Admin not found")
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "is_root": row["is_root"],
    }


@admin_router.get("/panel/api/admin/stats")
async def admin_stats(admin: dict = Depends(require_root_admin)):
    return await get_tenant_stats()


@admin_router.get("/panel/api/admin/tenants")
async def admin_list_tenants(admin: dict = Depends(require_root_admin)):
    return await get_all_tenants()


@admin_router.get("/panel/api/admin/tenants/{tenant_id}")
async def admin_get_tenant(tenant_id: int, admin: dict = Depends(require_root_admin)):
    tenant = await get_tenant_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@admin_router.post("/panel/api/admin/tenants")
async def admin_create_tenant(
    name: str = Form(...),
    slug: str = Form(...),
    x_screen_name: str = Form(""),
    admin_username: str = Form(...),
    admin_password: str = Form(...),
    admin_display_name: str = Form(...),
    admin: dict = Depends(require_root_admin),
):
    if await slug_exists(slug):
        raise HTTPException(status_code=400, detail="Slug already taken")
    hashed = hash_password(admin_password)
    tenant = await create_tenant(name, slug, x_screen_name, admin_username, hashed, admin_display_name)
    return tenant


@admin_router.post("/panel/api/admin/tenants/{tenant_id}/activate")
async def admin_activate_tenant(tenant_id: int, admin: dict = Depends(require_root_admin)):
    tenant = await update_tenant(tenant_id, is_active=True)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"success": True, "tenant": tenant}


@admin_router.post("/panel/api/admin/tenants/{tenant_id}/deactivate")
async def admin_deactivate_tenant(tenant_id: int, admin: dict = Depends(require_root_admin)):
    tenant = await update_tenant(tenant_id, is_active=False)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"success": True, "tenant": tenant}


@admin_router.post("/panel/api/admin/tenants/{tenant_id}/login-as")
async def admin_login_as(tenant_id: int, admin: dict = Depends(require_root_admin)):
    tenant = await get_tenant_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not tenant["is_active"]:
        raise HTTPException(status_code=400, detail="Tenant is deactivated")
    from database import get_all_admins
    admins = await get_all_admins(tenant_id)
    superadmin = next((a for a in admins if a["role"] == "superadmin"), None)
    if not superadmin:
        raise HTTPException(status_code=404, detail="No superadmin found for this tenant")
    token = create_token(superadmin["id"], superadmin["role"], tenant_id=tenant_id, is_root=False)
    return {"token": token, "slug": tenant["slug"], "role": superadmin["role"]}
