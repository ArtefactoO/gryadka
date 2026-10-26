// Synthetic API fixtures only. Never point this runner at a deployed service.
const { chromium } = require(process.env.GRYADKA_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fixture = require('./monitor-fixtures.cjs');
const base = process.env.GRYADKA_BROWSER_URL || 'http://127.0.0.1:4190';
const out = process.env.GRYADKA_BROWSER_ARTIFACTS;
if (!out || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Use a local preview and a writable artifact directory');
fs.mkdirSync(out, { recursive: true });
const cases = [['mongodb', 'mongo-demo', 'mongo'], ['rabbitmq', 'rabbit-demo', 'rabbit'], ['redis', 'redis-demo', 'redis'], ['postgresql', 'pg-demo', 'pg'], ['kafka', 'kafka-demo', 'kafka']];
const report = { scans: [], checks: [], errors: [], fontRequests: [] };
let currentPage;

async function tabTo(page, locator) {
  for (let i = 0; i < 150; i++) {
    if (await locator.evaluate(e => e === document.activeElement)) return;
    await page.keyboard.press('Tab');
  }
  throw Error('Unreachable by keyboard: ' + await locator.getAttribute('aria-label'));
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    report.browser = browser.version();
    for (const palette of ['signal', 'cobalt', 'orange']) for (const mode of ['dark', 'light']) for (const width of [320, 390, 768, 1440]) {
      if (process.env.GRYADKA_BROWSER_CASE && process.env.GRYADKA_BROWSER_CASE !== `${palette}:${mode}:${width}`) continue;
      report.currentCase = { palette, mode, width };
      const context = await browser.newContext({ viewport: { width, height: 1000 }, hasTouch: width <= 390, isMobile: width <= 390, reducedMotion: 'reduce', timezoneId: 'Europe/Moscow' });
      await context.addInitScript(({ user, palette, mode }) => {
        localStorage.setItem('auth_token', 'fixture-only');
        localStorage.setItem('auth_user', JSON.stringify(user));
        if (!localStorage.getItem('gryadka-ui')) localStorage.setItem('gryadka-ui', JSON.stringify({ palette, mode }));
      }, { user: fixture.user, palette, mode });
      let state = 'ready', historyVisible = false;
      const mongoHistory = structuredClone(fixture.mongo);
      mongoHistory.nodes[0].stateStr = 'SECONDARY'; mongoHistory.nodes[0].priority = 1;
      mongoHistory.nodes[0].lag = 14.5;
      mongoHistory.nodes[0].lag_history = [0, 4, 8, 14.5].map((lag, index) => ({ time: new Date(Date.now() - (3 - index) * 30_000).toISOString(), lag }));
      await context.route('**/api/**', async route => {
        const path = new URL(route.request().url()).pathname;
        let value = {}, status = 200;
        if (path === '/api/auth/me') value = fixture.user;
        else if (path === '/api/targets') value = fixture.targets.filter(t => t.type !== 'redpanda');
        else if (path === '/api/meta') value = { version: 'fixture-only' };
        else if (path === '/api/targets/unreachable/list') value = ['no_data', 'DEGRADED', 'DOWN'].includes(state) ? [{ host: 'kafka-demo', target_name: 'kafka-demo', target_type: 'kafka', status: state }] : [];
        else if (path.endsWith('/databases-overview')) value = path.includes('postgresql') ? { 'pg-demo': ['app', 'analytics'] } : { 'mongo-demo': ['app', 'analytics'] };
        else {
          const entry = cases.find(([kind]) => path.startsWith(kind === 'mongodb' ? '/api/clusters/' : '/api/' + kind + '/'));
          value = entry ? entry[0] === 'mongodb' && historyVisible ? mongoHistory : fixture[entry[2]] : {};
          if (state === 'loading') await new Promise(resolve => setTimeout(resolve, 3000));
          if (state === 'warmup') { status = 503; value = { detail: 'Свежие данные мониторинга пока не получены. Попробуйте обновить монитор позже.' }; }
        }
        if (path === '/api/events/stream') return route.fulfill({ contentType: 'text/event-stream', body: ': fixture\n\n' });
        return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
      });
      const page = await context.newPage();
      currentPage = page;
      page.on('pageerror', error => report.errors.push(error.message));
      page.on('request', request => { if (/fonts\.(googleapis|gstatic)\.com/.test(request.url())) report.fontRequests.push(request.url()); });
      for (const [type, target] of cases) {
        await page.goto(base + '/?type=' + type + '&target=' + target);
        await page.getByRole('heading', { name: 'Nodes', exact: true }).waitFor();
        assert.equal(await page.locator('html').getAttribute('lang'), 'ru');
        const scan = await page.evaluate(() => {
          const small = [...document.querySelectorAll('button,[role="tab"],[role="combobox"],[tabindex="0"],input')].filter(e => {
            if (e.disabled || !e.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
            const r = e.getBoundingClientRect();
            // An input's enclosing outlined field supplies its touch area.
            const hit = e.matches('input') ? e.closest('.MuiInputBase-root').getBoundingClientRect() : r;
            return hit.width < 43.9 || hit.height < 43.9;
          }).map(e => ({ text: e.textContent, label: e.getAttribute('aria-label'), rect: e.getBoundingClientRect().toJSON() }));
          return { overflow: document.documentElement.scrollWidth > innerWidth + 1, small };
        });
        assert.equal(scan.overflow, false, `${type}/${width} page overflow`);
        if (width <= 390) assert.deepEqual(scan.small, [], `${type}/${width} touch targets`);
        report.scans.push({ palette, mode, width, type, ...scan });
      }
      if (width === 1440) {
        const tab = page.getByRole('tab', { name: 'Consumer Groups', exact: true });
        await tabTo(page, page.getByRole('tab', { name: 'Overview', exact: true }));
        await page.keyboard.press('ArrowRight');
        assert.equal(await tab.evaluate(e => e === document.activeElement), true);
        await page.waitForFunction(el => getComputedStyle(el).outlineWidth === '2px', await tab.elementHandle(), { timeout: 2000 });
        assert.match(await tab.evaluate(e => getComputedStyle(e).outline), /2px/);
        await page.keyboard.press('Enter');
        const info = page.getByRole('button', { name: 'Пояснение к Offset Status', exact: true });
        await tabTo(page, info);
        await page.waitForFunction(el => getComputedStyle(el).outlineWidth === '2px', await info.elementHandle(), { timeout: 2000 });
        assert.match(await info.evaluate(e => getComputedStyle(e).outline), /2px/);
        await page.keyboard.press('Enter');
        await page.getByRole('tooltip').filter({ hasText: 'Доступность данных об offsets' }).waitFor();
        await page.keyboard.press('Escape');
        await page.getByRole('tooltip').waitFor({ state: 'hidden' });
        const chip = page.getByText('Cluster status: DEGRADED', { exact: true }).locator('..');
        await tabTo(page, chip);
        await page.waitForFunction(el => getComputedStyle(el).outlineWidth === '2px', await chip.elementHandle(), { timeout: 2000 });
        assert.match(await chip.evaluate(e => getComputedStyle(e).outline), /2px/);
        await page.screenshot({ path: `${out}/${palette}-${mode}-focus.png` });
        report.checks.push({ palette, mode, keyboardFocus: true, tooltipKeyboard: true });
      }
      historyVisible = true;
      await page.goto(base + '/?type=mongodb&target=mongo-demo');
      await page.getByRole('button', { name: /^Развернуть строку/ }).first().click();
      const figure = page.getByRole('figure', { name: 'История отставания узла ' + mongoHistory.nodes[0].name, exact: true });
      await figure.waitFor();
      assert.match(await figure.innerText(), /Последнее: 14,5 s/);
      assert.match(await figure.innerText(), /Минимум: 0 s, максимум: 14,5 s/);
      const labels = await figure.locator('.recharts-wrapper text').evaluateAll(elements => {
        const lum = rgb => rgb.slice(0, 3).map(value => { value /= 255; return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4; }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
        const rgb = value => (value.match(/[\d.]+/g) || []).map(Number);
        return elements.map(element => {
          let parent = element, background;
          while (parent) { background = rgb(getComputedStyle(parent).backgroundColor); if (background.length === 3 || background[3] > .99) break; parent = parent.parentElement; }
          const x = lum(rgb(getComputedStyle(element).fill)), y = lum(background);
          return { text: element.textContent, ratio: (Math.max(x, y) + .05) / (Math.min(x, y) + .05) };
        });
      });
      for (const text of ['Lag (s)', 'Normal', 'Warning', 'Critical']) assert.ok(labels.some(label => label.text === text), text + ' label is rendered');
      for (const label of labels) assert.ok(label.ratio >= 4.5, JSON.stringify({ palette, mode, label }));
      const measurements = figure.locator('summary');
      await measurements.focus(); await measurements.press('Enter');
      assert.equal(await figure.locator('details').getAttribute('open'), '');
      if (width <= 390) assert.ok((await measurements.boundingBox()).height >= 44);
      const table = figure.getByRole('table');
      await table.getByRole('cell', { name: '0', exact: true }).waitFor();
      await table.getByRole('cell', { name: '14,5', exact: true }).waitFor();
      assert.ok(!(await figure.ariaSnapshot()).includes('PM'));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      if (palette === 'signal' && width === 1440) await page.screenshot({ path: `${out}/${mode}-lag-history.png`, fullPage: true });
      report.checks.push({ palette, mode, width, lagHistory: true, keyboardMeasurements: true, minimumChartContrast: Math.min(...labels.map(label => label.ratio)) });
      historyVisible = false;

      const paletteControl = page.getByRole('button', { name: 'Выбрать цветовую систему', exact: true });
      await paletteControl.focus(); await paletteControl.press('Enter');
      const menu = page.getByRole('menu'); await menu.waitFor();
      for (const name of ['Mono', 'Cobalt', 'Orange']) await menu.getByRole('menuitemradio', { name, exact: true }).waitFor();
      await menu.getByRole('menuitemcheckbox', { name: 'System', exact: true }).waitFor();
      const menuStyle = await menu.locator('..').evaluate(element => ({ shadow: getComputedStyle(element).boxShadow, width: element.getBoundingClientRect().width }));
      assert.equal(menuStyle.shadow, 'none'); assert.ok(menuStyle.width <= 240);
      await page.waitForFunction(() => document.activeElement?.matches('[role="menuitemradio"], [role="menuitemcheckbox"]') && getComputedStyle(document.activeElement).outlineWidth === '2px');
      const menuFocus = await menu.evaluate(() => { const active = document.activeElement, style = getComputedStyle(active); return { visible: active.matches(':focus-visible'), outline: style.outlineWidth, offset: style.outlineOffset }; });
      assert.deepEqual(menuFocus, { visible: true, outline: '2px', offset: '-4px' });
      if (width === 1440 || palette === 'orange' && mode === 'dark' && width <= 390) await page.screenshot({ path: `${out}/${palette}-${mode}-menu-${width}.png` });
      await menu.getByRole('menuitemradio', { name: 'Cobalt', exact: true }).click();
      assert.equal(await page.evaluate(() => document.documentElement.dataset.palette), 'cobalt');
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('gryadka-ui')).mode), mode);
      await paletteControl.click(); await page.getByRole('menuitemcheckbox', { name: 'System', exact: true }).click();
      await page.emulateMedia({ colorScheme: mode === 'dark' ? 'light' : 'dark' });
      await page.waitForFunction(expected => document.documentElement.dataset.theme === expected, mode === 'dark' ? 'light' : 'dark');
      await paletteControl.click();
      assert.equal(await page.getByRole('menuitemcheckbox', { name: 'System', exact: true }).getAttribute('aria-checked'), 'true');
      await page.getByRole('menuitemcheckbox', { name: 'System', exact: true }).click();
      const manualMode = mode === 'dark' ? 'light' : 'dark';
      await page.emulateMedia({ colorScheme: mode });
      await page.reload(); await paletteControl.waitFor();
      assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('gryadka-ui'))), { palette: 'cobalt', mode: manualMode });
      assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), manualMode);
      report.checks.push({ palette, mode, width, compactThemeMenu: true, independentPaletteMode: true, systemToggle: true, persisted: true });
      await page.evaluate(prefs => localStorage.setItem('gryadka-ui', JSON.stringify(prefs)), { palette, mode });

      if (palette === 'signal' && mode === 'dark') {
        await page.goto(base + '/?type=rabbitmq&target=rabbit-demo');
        await page.getByRole('tab', { name: 'Queues', exact: true }).click();
        for (const label of ['Ready', 'Unacked']) {
          const control = page.getByRole('button', { name: 'Пояснение к ' + label, exact: true });
          if (width <= 390) await control.tap(); else await control.click();
          await page.getByRole('tooltip').waitFor();
          await page.keyboard.press('Escape');
          await page.getByRole('tooltip').waitFor({ state: 'hidden' });
        }
        await page.goto(base + '/?type=postgresql&target=pg-demo');
        await page.getByRole('heading', { name: 'Nodes', exact: true }).waitFor();
        const search = page.getByRole('button', { name: 'Поиск по базам PostgreSQL', exact: true });
        const input = page.getByRole('textbox', { name: 'Поиск по базам PostgreSQL', exact: true });
        assert.equal(await input.count(), 0);
        await search.click(); await input.waitFor(); await input.fill('orders'); await input.press('Escape');
        assert.equal(await input.count(), 0); assert.equal(await search.evaluate(e => e === document.activeElement), true);
        assert.equal(await search.getAttribute('aria-expanded'), 'false');
        assert.ok(!(await page.locator('body').ariaSnapshot()).includes('textbox "Поиск по базам PostgreSQL"'));
        state = 'loading'; await page.goto(base + '/?type=postgresql&target=pg-demo');
        await page.getByRole('status').filter({ hasText: 'Загрузка PostgreSQL' }).waitFor();
        assert.match(await page.locator('body').ariaSnapshot(), /status.*Загрузка PostgreSQL/);
        state = 'warmup'; await page.goto(base + '/?type=postgresql&target=pg-demo');
        await page.getByRole('alert').filter({ hasText: 'Свежие данные мониторинга пока не получены' }).waitFor();
        for (const status of ['no_data', 'DEGRADED', 'DOWN']) {
          state = status; await page.goto(base + '/?type=kafka&target=kafka-demo');
          await page.getByText('Требуют внимания:', { exact: true }).waitFor();
          const notice = page.getByRole('button').filter({ hasText: 'Kafka: kafka-demo:' });
          assert.match(await notice.innerText(), status === 'no_data' ? /NO DATA/ : new RegExp(status));
          assert.equal(await page.getByText('Недоступны:', { exact: true }).count(), 0);
        }
        report.checks.push({ width, headerTooltips: true, collapsedSearchAbsent: true, escapeFocus: true, loadingAnnounced: true, russianWarmup: true, bannerStates: true });
      }
      await context.close();
    }
    assert.deepEqual(report.errors, []); assert.deepEqual(report.fontRequests, []);
    report.success = true;
  } catch (error) { report.success = false; report.error = error.stack; if (currentPage && !currentPage.isClosed()) await currentPage.screenshot({ path: out + '/failure.png', fullPage: true }).catch(() => {}); }
  finally { await browser.close(); fs.writeFileSync(out + '/accessibility-report.json', JSON.stringify(report, null, 2)); console.log(JSON.stringify({ success: report.success, scans: report.scans.length, checks: report.checks.length, error: report.error })); }
  if (!report.success) process.exitCode = 1;
})();
