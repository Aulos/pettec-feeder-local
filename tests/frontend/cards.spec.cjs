const { test, expect } = require('@playwright/test');
const path = require('node:path');

async function setup(page, type = 'pettec-feed-card') {
  await page.setContent(`<style>
    body {margin:16px;background:#f5f6f8;font-family:Arial,sans-serif;--primary-color:#007b82;--primary-text-color:#202a2e;--secondary-text-color:#626c73;--divider-color:#d8e1e2;--card-background-color:white}
    ha-card {display:block;background:white;border-radius:12px}
  </style>`);
  await page.addScriptTag({ path: path.resolve('custom_components/pettec_local/frontend/pettec-cards.js') });
  await page.evaluate(type => {
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
  }, type);
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
