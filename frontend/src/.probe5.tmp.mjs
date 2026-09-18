import { chromium } from 'playwright';
const RAW=/\b(ACTIVE|PUBLISHED|DRAFT|ARCHIVED|RETIRED|PENDING_REVIEW|FOLLOWING|OBSERVING|CLOSED|IN_PROGRESS|NOT_STARTED|COMPLETED|KEY_ATTENTION|GENERAL_RANGE|REVIEWED|PLANNED|VALID|SCHOOL|GRADE|CLASS)\b/g;
const b=await chromium.launch(); const p=await b.newPage();
await p.goto('http://localhost:5173/login');
await p.getByRole('button',{name:'心理老师',exact:true}).click();
await p.getByRole('textbox',{name:/手机号/}).fill('13800000001');
await p.getByRole('textbox',{name:/密码/i}).fill('123456');
await p.getByRole('button',{name:'登录',exact:true}).click();
await p.waitForURL('**/counselor/workbench');
await p.goto('http://localhost:5173/counselor/cases/1');
await p.waitForLoadState('networkidle'); await p.waitForTimeout(800);
let t=await p.locator('main').innerText();
for(const re of [/S\d{3}/g,/MHT[-\w.]*/g]) t=t.replace(re,' ');
console.log('个案详情页 /counselor/cases/1 的原始编码:', [...new Set(t.match(RAW)||[])].join(', ')||'(无)');
const lines=t.split('\n');
console.log('\n含编码的行:');
lines.filter(l=>/\b(FOLLOWING|KEY_ATTENTION|VALID|PENDING_REVIEW|GENERAL_RANGE|REVIEWED|PLANNED)\b/.test(l)).slice(0,12).forEach(l=>console.log('  '+l.trim()));
// cycle all five tabs and re-scan
for(const tab of ['跟进记录','家庭回访','复测趋势','访问审计']){
  await p.locator(`.tab:has-text("${tab}")`).first().click(); await p.waitForTimeout(500);
  let x=await p.locator('main').innerText();
  for(const re of [/S\d{3}/g,/MHT[-\w.]*/g]) x=x.replace(re,' ');
  const hits=[...new Set(x.match(RAW)||[])];
  console.log(`页签「${tab}」原始编码:`, hits.join(', ')||'(无)');
}
await b.close();
