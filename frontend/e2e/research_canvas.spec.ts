import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const EVIDENCE_DIR = path.resolve(__dirname, '../../docs/evidence');

async function verifyModalIframeSvg(page: Page) {
  const modal = page.getByTestId('modal-single');
  await expect(modal).toBeVisible();
  const box = await modal.boundingBox();
  expect(box).not.toBeNull();
  // Verify ~80vw x 80vh modal dimensions
  expect(box!.width).toBeGreaterThan(1050);
  expect(box!.height).toBeGreaterThan(640);

  const iframe = modal.locator('iframe.preview-iframe');
  await expect(iframe).toBeVisible();
  const frame = page.frameLocator('[data-testid="modal-single"] iframe.preview-iframe');
  await expect(frame.locator('svg').first()).toBeVisible({ timeout: 20_000 });

  const svgCount = await frame.locator('svg').count();
  let mainSvgBox: { x: number; y: number; width: number; height: number } | null = null;
  for (let idx = 0; idx < svgCount; idx++) {
    const candidateBox = await frame.locator('svg').nth(idx).boundingBox();
    if (candidateBox && candidateBox.width > 200 && candidateBox.height > 150) {
      mainSvgBox = candidateBox;
      break;
    }
  }
  expect(mainSvgBox).not.toBeNull();

  const markCount = await frame
    .locator('svg path, svg circle, svg rect, svg line, svg polygon')
    .count();
  expect(markCount).toBeGreaterThanOrEqual(3);
  return { modal, frame, svgBox: mainSvgBox! };
}

test.describe('Research Canvas End-to-End Acceptance (S01-S12)', () => {
  test('full multi-agent workflow, T3 charts, deferred comments, rightward v2, long-press compare, S10 & S12', async ({
    page,
  }) => {
    // Start cleanly without stale localStorage workspace ID
    await page.addInitScript(() => {
      if (!sessionStorage.getItem('rc_e2e_init')) {
        localStorage.removeItem('whales_research_canvas_workspace_id');
        sessionStorage.setItem('rc_e2e_init', '1');
      }
    });

    await page.goto('/');
    await expect(page.getByRole('button', { name: /Run Research Task/i })).toBeVisible();

    // S01: Submit 3-chart prompt (statistical + collaboration network + hierarchical edge bundling)
    const s01Prompt =
      'Using the real research dataset, show AI publication trends, an author collaboration network, and a hierarchical bundled network view.';
    await page.getByLabel('Research prompt').fill(s01Prompt);
    await page.getByRole('button', { name: /Run Research Task/i }).click();

    await expect(page.locator('.workspace-split')).toBeVisible();
    const cards = page.locator('.canvas-card');
    await expect(cards).toHaveCount(3, { timeout: 210_000 });

    // Wait for thumbnail images to finish loading & fit all cards cleanly
    await page.getByRole('button', { name: /Fit All/i }).click();
    await page.waitForTimeout(400);

    // Assert vertical layout invariant (all 3 cards aligned in col 0, strictly increasing Y)
    const cardIds: string[] = [];
    const cardBoxes: { x: number; y: number; width: number; height: number }[] = [];
    for (let i = 0; i < 3; i++) {
      const c = cards.nth(i);
      const vid = await c.getAttribute('data-version-id');
      expect(vid).toBeTruthy();
      cardIds.push(vid!);
      const img = c.locator('img.canvas-card-thumb');
      await expect(img).toBeVisible();
      const natW = await img.evaluate((el: HTMLImageElement) => el.naturalWidth);
      expect(natW).toBeGreaterThan(0);
      const b = await c.boundingBox();
      expect(b).not.toBeNull();
      cardBoxes.push(b!);
    }
    expect(Math.abs(cardBoxes[0].x - cardBoxes[1].x)).toBeLessThan(8);
    expect(Math.abs(cardBoxes[1].x - cardBoxes[2].x)).toBeLessThan(8);
    expect(cardBoxes[1].y).toBeGreaterThan(cardBoxes[0].y + 100);
    expect(cardBoxes[2].y).toBeGreaterThan(cardBoxes[1].y + 100);

    await page.screenshot({ path: path.join(EVIDENCE_DIR, '01_canvas_three_artifacts.png') });

    // S07 + S08 + S09 + S02: Inspect all 3 artifacts in 80vw x 80vh modal
    const shotNames = [
      '02_modal_statistical.png',
      '03_modal_network.png',
      '04_modal_hierarchical_bundling.png',
    ];
    const runCountBeforeExplore = await page.locator('.task-card').count();
    for (let i = 0; i < 3; i++) {
      await cards.nth(i).click();
      const { frame, svgBox } = await verifyModalIframeSvg(page);

      // S02: Hover inside SVG & toggle an interactive control if present -> must NOT create new Agent runs
      await page.mouse.move(svgBox.x + svgBox.width / 2, svgBox.y + svgBox.height / 2);
      const selectCount = await frame.locator('select').count();
      if (selectCount > 0) {
        const firstSel = frame.locator('select').first();
        const optCount = await firstSel.locator('option').count();
        if (optCount > 1) {
          await firstSel.selectOption({ index: 1 });
        }
      }
      await page.waitForTimeout(250);
      await page.screenshot({ path: path.join(EVIDENCE_DIR, shotNames[i]) });
      await page.getByRole('button', { name: 'Minimize' }).click();
      await expect(page.getByTestId('modal-single')).toBeHidden();
    }
    expect(await page.locator('.task-card').count()).toBe(runCountBeforeExplore);

    // S03: Add anchored visual comment on card #1 (collaboration network) -> save only, NO new version
    const targetCard = cards.nth(1);
    const parentVersionId = await targetCard.getAttribute('data-version-id');
    expect(parentVersionId).toBeTruthy();

    await targetCard.click();
    await expect(page.getByTestId('modal-single')).toBeVisible();
    await page.getByRole('button', { name: /Comments/i }).click();
    await page.getByRole('button', { name: /Pin on Preview/i }).click();

    const overlay = page.getByTestId('pin-capture-overlay');
    await expect(overlay).toBeVisible();
    const ovBox = await overlay.boundingBox();
    expect(ovBox).not.toBeNull();
    await page.mouse.click(ovBox!.x + ovBox!.width * 0.50, ovBox!.y + ovBox!.height * 0.72);

    await page
      .getByPlaceholder(/Describe visual adjustment/i)
      .fill('节点重叠，请增加节点间距并突出高产作者标签');
    await page.getByRole('button', { name: 'Save Comment' }).click();

    const commentImg = page.locator('img.comment-shot').first();
    await expect(commentImg).toBeVisible({ timeout: 25_000 });
    const cNatW = await commentImg.evaluate((el: HTMLImageElement) => el.naturalWidth);
    expect(cNatW).toBeGreaterThan(0);

    await page.screenshot({ path: path.join(EVIDENCE_DIR, '05_pinned_comment_saved.png') });
    await page.getByRole('button', { name: 'Minimize' }).click();

    // Still 3 cards on Canvas; card #1 shows pending_comment_count = 1
    await expect(cards).toHaveCount(3);
    await expect(targetCard.locator('[data-pending-comments="1"]')).toBeVisible();

    // S04: Trigger revision from Chat -> v2 placed to the RIGHT of parent v1
    await page
      .getByLabel('Follow-up prompt')
      .fill('请应用合作网络图尚未解决的评论，改善节点标签可读性并增大间距。');
    await page.getByRole('button', { name: 'Send' }).click();

    await expect(cards).toHaveCount(4, { timeout: 150_000 });
    await page.getByRole('button', { name: /Fit All/i }).click();
    await page.waitForTimeout(400);

    const v2Card = cards.nth(3);
    const v2Id = await v2Card.getAttribute('data-version-id');
    expect(v2Id).toBeTruthy();
    expect(v2Id).not.toBe(parentVersionId);

    const v1BoxAfter = await page
      .locator(`.canvas-card[data-version-id="${parentVersionId}"]`)
      .boundingBox();
    const v2Box = await page.locator(`.canvas-card[data-version-id="${v2Id}"]`).boundingBox();
    expect(v1BoxAfter).not.toBeNull();
    expect(v2Box).not.toBeNull();
    // Rightward evolution invariant
    expect(v2Box!.x).toBeGreaterThan(v1BoxAfter!.x + 150);
    // Old card's pending comment badge cleared after addressing
    await expect(
      page.locator(`.canvas-card[data-version-id="${parentVersionId}"] [data-pending-comments]`)
    ).toHaveCount(0);

    await page.screenshot({ path: path.join(EVIDENCE_DIR, '06_rightward_version_v2.png') });

    // S05 + S06: First test 1-card guard, then real blank-canvas long-press marquee across v1 & v2
    await page
      .locator(`.canvas-card[data-version-id="${parentVersionId}"]`)
      .click({ modifiers: ['Shift'] });
    await page.mouse.click(v1BoxAfter!.x - 24, v1BoxAfter!.y, { button: 'right' });
    const disabledCompare = page.getByRole('button', { name: /Compare Selected/i });
    await expect(disabledCompare).toHaveAttribute('aria-disabled', 'true');
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');

    // Blank-canvas left-button long-press (>= 360ms stationary) + drag across v1 and v2
    const canvasBox = await page.getByTestId('infinite-canvas').boundingBox();
    expect(canvasBox).not.toBeNull();
    const startX = Math.max(canvasBox!.x + 8, v1BoxAfter!.x - 18);
    const startY = Math.max(canvasBox!.y + 8, v1BoxAfter!.y - 12);
    const endX = Math.min(canvasBox!.x + canvasBox!.width - 12, v2Box!.x + v2Box!.width + 18);
    const endY = Math.min(canvasBox!.y + canvasBox!.height - 12, v2Box!.y + v2Box!.height + 10);

    await page.mouse.move(startX, startY);
    await page.mouse.down({ button: 'left' });
    await page.waitForTimeout(420); // exceeds 320ms LONG_PRESS_MS
    await page.mouse.move(endX, endY, { steps: 10 });
    await page.mouse.up({ button: 'left' });

    await expect(page.getByText('2 selected')).toBeVisible();
    await page.mouse.click(endX + 10, endY + 10, { button: 'right' });
    const activeCompare = page.getByRole('button', { name: /Compare Selected \(2\/2\)/i });
    await expect(activeCompare).not.toHaveAttribute('aria-disabled', 'true');
    await activeCompare.click();

    const compareModal = page.getByTestId('modal-compare');
    await expect(compareModal).toBeVisible();
    const windows = compareModal.locator('.artifact-window');
    await expect(windows).toHaveCount(2);

    // S06: Independent Data Map checks inside both Compare panes
    await windows.nth(0).getByRole('button', { name: 'Data Map' }).click();
    await windows.nth(1).getByRole('button', { name: 'Data Map' }).click();

    const stagesLeft = windows.nth(0).locator('.datamap-node-btn');
    const stagesRight = windows.nth(1).locator('.datamap-node-btn');
    await expect(stagesLeft).toHaveCount(6);
    await expect(stagesRight).toHaveCount(6);

    const snapLabelLeft = await windows
      .nth(0)
      .locator('.datamap-node-btn[data-stage="dataset_snapshot"]')
      .innerText();
    const snapLabelRight = await windows
      .nth(1)
      .locator('.datamap-node-btn[data-stage="dataset_snapshot"]')
      .innerText();
    expect(snapLabelLeft).toEqual(snapLabelRight);

    await page.screenshot({ path: path.join(EVIDENCE_DIR, '07_compare_dual_datamap.png') });

    // Check View Source inside right Compare pane
    await windows.nth(1).getByRole('button', { name: 'View Source' }).click();
    await expect(windows.nth(1).getByTestId('source-view')).toContainText('d3.v7.min.js');
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '08_view_source.png') });

    // Close Compare modal cleanly via Esc
    await page.keyboard.press('Escape');
    await expect(compareModal).toBeHidden();

    // S10: Generalizability — unseen 4th chart query (topic-by-year heatmap)
    await page
      .getByLabel('Follow-up prompt')
      .fill('Create an interactive topic-by-year heatmap with hover explanations and year filtering.');
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(cards).toHaveCount(5, { timeout: 150_000 });
    await page.getByRole('button', { name: /Fit All/i }).click();
    await page.waitForTimeout(400);

    await cards.nth(4).click();
    await verifyModalIframeSvg(page);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '09_s10_unseen_heatmap.png') });
    await page.getByRole('button', { name: 'Minimize' }).click();

    // S12: Reload page and verify full persistence recovery from SQLite
    await page.reload();
    await expect(page.locator('.workspace-split')).toBeVisible();
    await expect(page.locator('.canvas-card')).toHaveCount(5);
    await page.getByRole('button', { name: /Fit All/i }).click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '10_reloaded_persistence.png') });
  });
});
