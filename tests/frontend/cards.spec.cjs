const { test, expect } = require('@playwright/test');
const path = require('node:path');

async function setup(page, type = 'pettec-feed-card', language = 'en') {
  await page.setContent(`<style>
    body {margin:16px;background:#f5f6f8;font-family:Arial,sans-serif;--primary-color:#007b82;--primary-text-color:#202a2e;--secondary-text-color:#626c73;--divider-color:#d8e1e2;--card-background-color:white}
    ha-card {display:block;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px}
  </style>`);
  await page.addScriptTag({content: `
    customElements.define('ha-card', class extends HTMLElement {
      constructor() {
        super();
        this.attachShadow({mode:'open'}).innerHTML = '<style>:host{display:block;box-sizing:border-box;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px}</style><slot></slot>';
      }
    });
  `});
  await page.addScriptTag({ path: path.resolve('custom_components/pettec_local/frontend/pettec-cards.js') });
  await page.evaluate(([type, language]) => {
    const registry = [];
    const states = {};
    const add = (domain, suffix, state) => {
      const id = `${domain}.renamed_${suffix}`;
      registry.push({entity_id: id, unique_id: `entry_${suffix}`, platform: 'pettec_local', device_id: 'feeder'});
      states[id] = {state: String(state), attributes: {}};
    };
    add('button', 'feed', 'unknown'); // Buttons are unknown before their first press.
    add('number', 'quantity', 25);
    for (let slot = 1; slot <= 5; slot++) {
      add('time', `schedule_${slot}_schedule_time`, '08:30:00');
      add('number', `schedule_${slot}_schedule_amount`, 25);
      add('select', `schedule_${slot}_schedule_days`, 'Every day');
      add('switch', `schedule_${slot}_schedule_enabled`, 'on');
    }
    window.calls = [];
    window.hass = {
      locale: {language},
      states,
      callWS: async ({type}) => type.includes('entity_registry') ? registry : [{id: 'feeder', name: 'Kitchen feeder'}],
      callService: async (domain, service, data) => {
        window.calls.push({domain, service, data});
        if (window.waitForService) await new Promise(resolve => {window.finishService = resolve;});
        if (window.failService) throw new Error('Connection timed out.');
      },
    };
    window.card = document.createElement(type);
    card.setConfig({type: `custom:${type}`, device_id: 'feeder'});
    document.body.append(card);
    card.hass = hass;
  }, [type, language]);
}

test('feed uses entered amount exactly once while pending and never retries errors', async ({page}) => {
  await setup(page);
  await page.getByLabel('Amount · g').fill('42');
  await page.evaluate(() => {window.waitForService = true;});
  await page.getByRole('button', {name: 'Dispense', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Dispense', exact: true})).toBeDisabled();
  expect(await page.evaluate(() => calls)).toEqual([{domain: 'pettec_local', service: 'dispense', data: {entity_id: 'button.renamed_feed', quantity: 42}}]);
  await page.evaluate(() => {window.failService = true; finishService();});
  await expect(page.getByRole('status')).toContainText('Check the bowl');
  expect(await page.evaluate(() => calls.length)).toBe(1);
});

test('card titles link to the selected feeder device page', async ({page}) => {
  await setup(page);
  await expect(page.locator('pettec-feed-card').locator('a.device-link')).toHaveAttribute('href', '/config/devices/device/feeder');
  await expect(page.locator('pettec-feed-card').locator('a.device-link')).toHaveAccessibleName('Open feeder device page');
  await setup(page, 'pettec-schedule-card');
  await expect(page.locator('pettec-schedule-card').locator('a.device-link')).toHaveAttribute('href', '/config/devices/device/feeder');
});

test('invalid feed amount and unavailable feeder cannot dispense', async ({page}) => {
  await setup(page);
  await page.getByLabel('Amount · g').fill('201');
  await page.getByRole('button', {name: 'Dispense', exact: true}).click();
  expect(await page.evaluate(() => calls.length)).toBe(0);
  await page.evaluate(() => {hass.states['button.renamed_feed'].state = 'unavailable'; card.hass = hass;});
  await expect(page.getByRole('button', {name: 'Dispense', exact: true})).toBeDisabled();
});

test('schedule saves a complete meal and keeps draft edits across HA updates', async ({page}) => {
  await setup(page, 'pettec-schedule-card');
  const meal = page.locator('fieldset').first();
  await meal.getByLabel('Time', {exact:true}).fill('19:15');
  await meal.getByLabel('Amount · g').fill('35');
  for (const day of ['Tue','Wed','Thu','Fri','Sat']) await meal.getByRole('button', {name:day, exact:true}).click();
  await page.evaluate(() => {card.hass = {...hass};});
  await expect(meal.getByLabel('Time', {exact:true})).toHaveValue('19:15');
  expect(await page.evaluate(() => calls.length)).toBe(0);
  await meal.getByRole('button', {name:'Save meal'}).click();
  expect(await page.evaluate(() => calls[0].data)).toEqual({
    entity_id:'time.renamed_schedule_1_schedule_time', slot:1, enabled:true,
    hour:19, minute:15, quantity:35, weekdays:65,
    expected:{enabled:true,hour:8,minute:30,quantity:25,weekdays:127},
  });
  await expect(meal.getByRole('status')).toContainText('Saved and verified');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'test-results/schedule-mobile.png',fullPage:true});
});

test('stale schedule draft is refreshed without a write', async ({page}) => {
  await setup(page, 'pettec-schedule-card');
  const meal = page.locator('fieldset').first();
  await meal.getByLabel('Amount · g').fill('35');
  await page.evaluate(() => {hass.states['number.renamed_schedule_1_schedule_amount'].state = '40'; card.hass = hass;});
  await meal.getByRole('button', {name:'Save meal'}).click();
  await expect(meal.getByRole('status')).toContainText('changed elsewhere');
  await expect(meal.getByLabel('Amount · g')).toHaveValue('40');
  expect(await page.evaluate(() => calls.length)).toBe(0);
});

test('card editor selects a feeder without entering entity IDs', async ({page}) => {
  await setup(page);
  await page.evaluate(() => {
    const editor = document.createElement('pettec-card-editor');
    editor.setConfig({type:'custom:pettec-feed-card'});
    editor.addEventListener('config-changed', e => {window.savedConfig = e.detail.config;});
    document.body.append(editor); editor.hass = hass;
  });
  await page.getByLabel('Feeder', {exact:true}).selectOption('feeder');
  expect(await page.evaluate(() => savedConfig)).toEqual({type:'custom:pettec-feed-card',device_id:'feeder'});
});

test('compact feed card adapts to its container in dark and light themes', async ({page}) => {
  await setup(page);
  await page.setViewportSize({width:760,height:1000});
  await page.evaluate(() => {
    card.remove();
    document.body.style.margin = '24px';
    document.body.style.background = '#101214';
    const title = document.createElement('h1');
    title.textContent = 'PetTec · Feed card preview';
    title.style.cssText = 'color:#e8edef;font-size:20px;margin:0 0 24px';
    document.body.append(title);
    for (const [theme, width] of [['dark',240],['dark',360],['dark',600],['light',360]]) {
      const section = document.createElement('section');
      section.style.cssText = `width:${width}px;max-width:100%;margin-bottom:24px`;
      const caption = document.createElement('p');
      caption.textContent = `${width} px · ${theme}`;
      caption.style.cssText = 'color:#a3acb2;font-size:12px;margin:0 0 8px';
      const c = document.createElement('pettec-feed-card');
      c.style.cssText = theme === 'dark'
        ? '--primary-color:#00a5c1;--primary-text-color:#e5e8ea;--secondary-text-color:#a4acb3;--divider-color:#394047;--card-background-color:#1c2024'
        : '--primary-color:#007b82;--primary-text-color:#202a2e;--secondary-text-color:#626c73;--divider-color:#d8e1e2;--card-background-color:#fff';
      c.setConfig({type:'custom:pettec-feed-card',device_id:'feeder'});
      section.append(caption,c); document.body.append(section); c.hass = hass;
    }
  });
  await expect(page.getByRole('button',{name:'Dispense',exact:true})).toHaveCount(4);
  const layout = await page.evaluate(() => [...document.querySelectorAll('pettec-feed-card')].map(c => {
    const root = c.shadowRoot;
    const header = root.querySelector('.feed-header').getBoundingClientRect();
    const form = root.querySelector('.feed').getBoundingClientRect();
    return {width:c.clientWidth,height:c.clientHeight,overflow:root.querySelector('ha-card').scrollWidth > c.clientWidth,oneRow:form.left > header.left + header.width - 1};
  }));
  await page.screenshot({path:'test-results/feed-design-preview.png',fullPage:true});
  expect(layout.every(item => !item.overflow)).toBe(true);
  expect(layout[0].height).toBeLessThan(155);
  expect(layout[1].height).toBeLessThan(155);
  expect(layout[2].height).toBeLessThan(105);
  expect(layout[2].oneRow).toBe(true);
  await page.screenshot({path:'test-results/feed-design-preview.png',fullPage:true});
});

test('Polish feed card translates controls, editor and feedback', async ({page}) => {
  await setup(page, 'pettec-feed-card', 'pl-PL');
  await expect(page.getByRole('heading', {name:'Nakarm teraz'})).toBeVisible();
  await page.getByLabel('Ilość · g').fill('37');
  await page.getByRole('button', {name:'Podaj karmę', exact:true}).click();
  expect(await page.evaluate(() => calls[0].data.quantity)).toBe(37);
  await expect(page.getByRole('status')).toContainText('Karmnik potwierdził');
  await page.evaluate(() => {
    const editor = document.createElement('pettec-card-editor');
    editor.setConfig({type:'custom:pettec-feed-card'});
    document.body.append(editor); editor.hass = hass;
  });
  await expect(page.getByLabel('Karmnik', {exact:true})).toBeVisible();
  await page.evaluate(() => {window.failService = true;});
  await page.getByRole('button', {name:'Podaj karmę', exact:true}).click();
  await expect(page.getByRole('status')).toContainText('sprawdź miskę');
});

test('Polish weekday labels preserve the protocol mask and fit a narrow card', async ({page}) => {
  await setup(page, 'pettec-schedule-card', 'pl');
  await expect(page.getByRole('heading', {name:'Harmonogram karmienia'})).toBeVisible();
  const meal = page.locator('fieldset').first();
  for (const day of ['Wt.','Śr.','Czw.','Pt.','Sob.']) await meal.getByRole('button', {name:day,exact:true}).click();
  await meal.getByRole('button', {name:'Zapisz posiłek'}).click();
  expect(await page.evaluate(() => calls[0].data.weekdays)).toBe(65);
  expect(await page.evaluate(() => calls[0].data.expected.weekdays)).toBe(127);
  await expect(meal.getByRole('status')).toContainText('Zapisano i potwierdzono');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
