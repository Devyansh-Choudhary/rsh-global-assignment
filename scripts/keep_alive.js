// Keeps the Streamlit Community Cloud app awake by visiting it in a real browser.
// Free apps sleep after a period without visitors; a plain HTTP ping does not count as a visit.
// If the app is asleep, this clicks "Yes, get this app back up!" and waits until the dashboard renders.
// Usage: node scripts/keep_alive.js [url]      (run every 6 hours by .github/workflows/keep-alive.yml)
const { chromium } = require('playwright');

const URL = process.argv[2] || process.env.APP_URL || 'https://rsh-global-assignment-vnqzytwvnjmxkufwffa8z3.streamlit.app/';

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForTimeout(8000);

  const wake = page.getByRole('button', { name: /get this app back up/i });
  if (await wake.count()) {
    console.log('App was asleep - waking it up');
    await wake.first().click();
  }

  // The app renders inside an iframe; wait for the Monday brief headline.
  let title = '';
  for (let i = 0; i < 300 && !title; i++) {
    await page.waitForTimeout(1000);
    const app = page.frames().find((f) => /~\/\+\//.test(f.url()));
    if (!app) continue;
    try {
      const t = await app.locator('.jc-title').allInnerTexts();
      if (t.length) title = t.join(' / ');
    } catch (e) { /* frame reloading while the app boots */ }
  }
  await browser.close();
  if (!title) {
    console.error('App did not render within 5 minutes');
    process.exit(1);
  }
  console.log(`App is up: "${title}"`);
})();
