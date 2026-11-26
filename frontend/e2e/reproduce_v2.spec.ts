import { expect, test } from '@playwright/test';

/**
 * Reproduce V2 — Frozen L1.5, UI Refinements & Dynamic Multi-Instance Agents E2E Suite
 * Covers:
 * - REG-01: Data page, Reproduce Homepage, and inline Data <-> Reproduce switch regression
 * - UI-01: Initial 6-role roster visible on frame 1; Main left Boss, 5 right; no welcome card / no Reproduce badge
 * - UI-03 & API-05: Clicking/hovering Agent triggers 0 body/face changes and 0 task/chat POSTs; flat context chip (no nested border)
 * - UI-04..UI-09: Dynamic multi-instance specialists (`persona_analysis_2`, `persona_data_2`) morphing 2->3->5 on same <path>, Fit All + horizontal zoom scrub, In-Chat Viewer
 * - UI-POP: `ModelThinkingSelector` popover opens cleanly inside viewport without clipping in narrow left chat sidebar
 */

test('REG-01 & UI-01 & UI-03 & UI-POP: Homepage switch, 6-role roster, UI cleanups, viewport-safe popover & silent Agent click', async ({
  page,
}) => {
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.reload();

  // Verify Data homepage & debug button present in Data mode
  await expect(page.locator('h1.landing-title')).toContainText('Ask Any Questions of');
  await expect(page.getByTestId('debug-entry-btn')).toBeVisible();

  // Switch to Reproduce Homepage via inline Data -> Reproduce button
  await page.getByTestId('landing-mode-switch').click();
  await expect(page.locator('h1.landing-title')).toContainText('Upload Papers to');
  await expect(page.getByTestId('landing-mode-switch')).toHaveText('Reproduce');
  await expect(page.getByTestId('debug-entry-btn')).toHaveCount(0);

  // Create a Reproduce workspace via REST without submitting a chat task (tests UI-01 clean empty state)
  const uniqueTitle = `E2E Initial Roster ${Date.now()}`;
  const createdWs = await page.evaluate(async (title) => {
    const res = await fetch('/api/workspaces', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, workspace_type: 'reproduce' }),
    });
    return res.json();
  }, uniqueTitle);

  const writeRequestsAfterLoad: string[] = [];
  await page.reload();
  await expect(page.getByTestId('landing-mode-switch')).toHaveText('Reproduce');
  await page.getByRole('button', { name: uniqueTitle }).click();

  await expect(page.getByTestId('living-research-canvas')).toBeVisible();

  // UI cleanup assertions: no Reproduce header pill, no welcome card, placeholder is "Ask anything...", no Pause motion
  await expect(page.locator('.rep-run-badge')).toHaveCount(0);
  await expect(page.getByTestId('reproduce-chat-empty')).toHaveCount(0);
  await expect(page.getByTestId('reproduce-chat-input')).toHaveAttribute('placeholder', 'Ask anything');
  await expect(page.getByTestId('toggle-canvas-motion-btn')).toHaveCount(0);

  // Data-style horizontal scrub zoom pill + Fit All button must be visible on Canvas
  await expect(page.getByTestId('reproduce-zoom-scrub')).toBeVisible();
  await expect(page.getByTestId('reproduce-fit-all-btn')).toBeVisible();

  // Attach button and Enter/Send button stay horizontally aligned on one row
  const attachBox = await page.locator('.rep-chat-sidebar .btn-attach-icon').boundingBox();
  const sendBox = await page.getByTestId('reproduce-chat-send').boundingBox();
  expect(attachBox).not.toBeNull();
  expect(sendBox).not.toBeNull();
  if (attachBox && sendBox) {
    const attachMidY = attachBox.y + attachBox.height / 2;
    const sendMidY = sendBox.y + sendBox.height / 2;
    expect(Math.abs(attachMidY - sendMidY)).toBeLessThanOrEqual(2);
  }

  // ModelThinkingSelector popover must open inside viewport without getting clipped by narrow sidebar
  const modelTrigger = page.locator('.rep-chat-sidebar .model-selector-trigger');
  await modelTrigger.click();
  const popoverEl = page.locator('.rep-chat-sidebar .model-selector-popover');
  await expect(popoverEl).toBeVisible();
  const popBox = await popoverEl.boundingBox();
  expect(popBox).not.toBeNull();
  if (popBox) {
    expect(popBox.x).toBeGreaterThanOrEqual(0);
    expect(popBox.y).toBeGreaterThanOrEqual(0);
    expect(popBox.x + popBox.width).toBeLessThanOrEqual(1440);
    expect(popBox.y + popBox.height).toBeLessThanOrEqual(900);
  }
  await page.keyboard.press('Escape');
  await expect(popoverEl).not.toBeVisible();

  // All 6 initial role personas visible immediately on frame 1
  const expectedIds = [
    'persona_main',
    'persona_paper',
    'persona_data',
    'persona_analysis',
    'persona_validation',
    'persona_support',
  ];
  for (const pid of expectedIds) {
    const node = page.getByTestId(`persona-${pid}`);
    await expect(node).toBeVisible();
    await expect(node).toHaveAttribute('data-persona-state', 'idle');
  }
  await expect(page.getByTestId('persona-persona_main')).toContainText('Boss');

  // Main in left Boss zone, Specialists on right
  const mainX = Number(await page.getByTestId('persona-persona_main').getAttribute('data-world-x'));
  const analysisX = Number(
    await page.getByTestId('persona-persona_analysis').getAttribute('data-world-x')
  );
  expect(mainX).toBeLessThanOrEqual(260);
  expect(analysisX).toBeGreaterThan(300);

  // Intercept any network POST/PUT/PATCH/DELETE while interacting with Canvas (API-05 & UI-03)
  page.on('request', (req) => {
    if (req.method() !== 'GET' && req.url().includes('/api/')) {
      writeRequestsAfterLoad.push(`${req.method()} ${req.url()}`);
    }
  });

  const valNode = page.getByTestId('persona-persona_validation');
  const beforeClick = await valNode.evaluate((el) => ({
    x: el.getAttribute('data-world-x'),
    y: el.getAttribute('data-world-y'),
    state: el.getAttribute('data-persona-state'),
    faceTransform: el.querySelector('.rep-face')?.getAttribute('transform') || '',
  }));

  await valNode.hover({ force: true });
  await valNode.click({ force: true });
  await valNode.dblclick({ force: true });

  const afterClick = await valNode.evaluate((el) => ({
    x: el.getAttribute('data-world-x'),
    y: el.getAttribute('data-world-y'),
    state: el.getAttribute('data-persona-state'),
    faceTransform: el.querySelector('.rep-face')?.getAttribute('transform') || '',
  }));

  // Direct agent click is disabled: zero body/face reaction, zero notice/context chip, zero write POSTs
  expect(afterClick).toEqual(beforeClick);
  const pointerEvents = await valNode.evaluate((el) => getComputedStyle(el).pointerEvents);
  expect(pointerEvents).toBe('none');
  await expect(page.getByTestId('silent-agent-empty-notice')).toHaveCount(0);
  await expect(page.getByTestId('selected-context-chip')).toHaveCount(0);
  expect(writeRequestsAfterLoad).toEqual([]);

  expect(createdWs.workspace_id).toBeTruthy();
});

test('UI-04..UI-10: Dynamic multi-instance agents, 2->3->5 morphing without remount, Fit All, SIMULATION badge & In-Chat Viewer', async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });

  const runId = 'run-e2e-sim';
  const basePersonas = [
    { persona_id: 'persona_main', role: 'main', label: 'Boss', runtime_agent_id: null, state: 'idle', is_main: true, task_ids: [] },
    { persona_id: 'persona_paper', role: 'paper', label: 'Paper', runtime_agent_id: null, state: 'idle', is_main: false, task_ids: [] },
    { persona_id: 'persona_data', role: 'data', label: 'Data', runtime_agent_id: null, state: 'idle', is_main: false, task_ids: [] },
    { persona_id: 'persona_analysis', role: 'analysis', label: 'Analysis', runtime_agent_id: 'ag-1', state: 'debating', is_main: false, task_ids: ['t1'] },
    { persona_id: 'persona_validation', role: 'validation', label: 'Validation', runtime_agent_id: 'ag-2', state: 'debating', is_main: false, task_ids: ['t2'] },
    { persona_id: 'persona_support', role: 'support', label: 'Support', runtime_agent_id: null, state: 'idle', is_main: false, task_ids: [] },
  ];
  const expandedPersonas = [
    ...basePersonas,
    { persona_id: 'persona_data_2', role: 'data', label: 'Data #2', runtime_agent_id: 'ag-d2', state: 'debating', is_main: false, task_ids: ['td2'] },
    { persona_id: 'persona_analysis_2', role: 'analysis', label: 'Analysis #2', runtime_agent_id: 'ag-a2', state: 'debating', is_main: false, task_ids: ['ta2'] },
    { persona_id: 'persona_analysis_3', role: 'analysis', label: 'Analysis #3', runtime_agent_id: 'ag-a3', state: 'debating', is_main: false, task_ids: ['ta3'] },
  ];

  const snapshotsByStep = [
    // Step 0: 2-person collaboration -> rectangle table
    {
      at_seq: 17,
      personas: basePersonas,
      collaborations: [
        {
          collaboration_id: 'collab-17',
          kind: 'debate',
          status: 'active',
          topic: 'Citation window comparability',
          participant_persona_ids: ['persona_analysis', 'persona_validation'],
          ref: { kind: 'debate', id: 'debate-03' },
          last_event_seq: 17,
        },
      ],
    },
    // Step 1: 3-person collaboration -> morphs SAME SVG path to circle
    {
      at_seq: 20,
      personas: basePersonas,
      collaborations: [
        {
          collaboration_id: 'collab-17',
          kind: 'pod',
          status: 'active',
          topic: 'Citation window comparability',
          participant_persona_ids: ['persona_analysis', 'persona_validation', 'persona_data'],
          ref: { kind: 'debate', id: 'debate-03' },
          last_event_seq: 20,
        },
      ],
    },
    // Step 2: 5-person collaboration including dynamic multi-instance workers -> morphs SAME SVG path to ellipse
    {
      at_seq: 21,
      personas: expandedPersonas,
      collaborations: [
        {
          collaboration_id: 'collab-17',
          kind: 'pod',
          status: 'active',
          topic: 'Cross-role & multi-instance review',
          participant_persona_ids: [
            'persona_analysis',
            'persona_validation',
            'persona_data',
            'persona_analysis_2',
            'persona_data_2',
          ],
          ref: { kind: 'debate', id: 'debate-03' },
          last_event_seq: 21,
        },
      ],
    },
    // Step 3: 3 simultaneous collaborations (1 overlapping) -> max 2 prominent tables, all 8 unique personas present
    {
      at_seq: 22,
      personas: expandedPersonas,
      collaborations: [
        {
          collaboration_id: 'collab-17',
          kind: 'debate',
          status: 'active',
          topic: 'Window check',
          participant_persona_ids: ['persona_analysis', 'persona_validation'],
          ref: { kind: 'debate', id: 'd1' },
          last_event_seq: 22,
        },
        {
          collaboration_id: 'collab-18',
          kind: 'pod',
          status: 'active',
          topic: 'Schema check',
          participant_persona_ids: ['persona_paper', 'persona_data'],
          ref: { kind: 'pod', id: 'p1' },
          last_event_seq: 22,
        },
        {
          collaboration_id: 'collab-19',
          kind: 'debate',
          status: 'active',
          topic: 'Overlapping third table',
          participant_persona_ids: ['persona_validation', 'persona_support'],
          ref: { kind: 'debate', id: 'd2' },
          last_event_seq: 22,
        },
      ],
    },
    // Step 4: Ended -> 0 tables, all 8 personas (baseline 6 + 2 dynamic) spread cleanly at anchors
    {
      at_seq: 25,
      personas: expandedPersonas,
      collaborations: [],
    },
    // Step 5: Validation subagent walked up in front of Main presenting at the PPT Whiteboard Easel
    {
      at_seq: 28,
      personas: expandedPersonas.map((p) =>
        p.persona_id === 'persona_validation' ? { ...p, state: 'reporting' } : p
      ),
      collaborations: [],
    },
  ];

  await page.route('**/api/workspaces/*/reproduce-run*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: runId,
        status: 'paused',
        created_at: '2026-11-26T10:00:00Z',
        mode: 'simulation',
        title: 'Simulation Fixture Test',
      }),
    });
  });

  await page.route(`**/api/runs/${runId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: runId,
        status: 'paused',
        created_at: '2026-11-26T10:00:00Z',
        mode: 'simulation',
        title: 'Simulation Fixture Test',
      }),
    });
  });

  await page.route(`**/api/runs/${runId}/snapshot*`, async (route) => {
    const url = new URL(route.request().url());
    const atSeqParam = url.searchParams.get('at_seq');
    const stepIndex =
      atSeqParam !== null
        ? Math.min(snapshotsByStep.length - 1, Number(atSeqParam))
        : 0;
    const stepObj = snapshotsByStep[stepIndex];
    const personasPayload =
      atSeqParam === null
        ? [
            ...stepObj.personas,
            {
              persona_id: 'persona_analysis_2',
              role: 'analysis',
              label: 'Analysis #2',
              runtime_agent_id: 'ag-a2',
              state: 'completed',
              is_main: false,
              task_ids: ['ta2'],
            },
          ]
        : stepObj.personas;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: runId,
        at_seq: 4,
        agents: personasPayload,
        tasks: [],
        debates: [],
        pods: [],
        evidence: [],
        resources: {
          limits: { max_active_agent_tasks: 4, max_parallel_experiments: 2, exploration_budget_fraction: 0.2 },
          active_agent_tasks: 0,
          active_experiments: 0,
          exploration_used_fraction: 0.12,
        },
        canvas: {
          run_id: runId,
          at_seq: stepObj.at_seq,
          mode: 'simulation',
          personas: personasPayload,
          collaborations: stepObj.collaborations,
        },
      }),
    });
  });

  const mockChatMessages = [
    {
      message_id: 'm0',
      run_id: runId,
      kind: 'message',
      role: 'user',
      created_at: '2026-11-26T09:59:50Z',
      text: 'reproduce it and respond in Chinese',
      related_refs: [],
      artifact_ids: [],
      seq: 1,
    },
    {
      message_id: 'm1',
      run_id: runId,
      kind: 'activity',
      role: 'activity',
      created_at: '2026-11-26T10:00:00Z',
      text: 'Analysis and Validation started citation window review.',
      related_refs: [{ kind: 'collaboration', id: 'collab-17' }],
      artifact_ids: ['art-csv-1'],
      seq: 17,
    },
    {
      message_id: 'm2',
      run_id: runId,
      kind: 'message',
      role: 'main',
      created_at: '2026-11-26T10:01:00Z',
      text: '### Reproduction Summary\n\n- Verified effect delta $\\hat{\\delta} = 0.68$ with $p = 0.0049$.',
      related_refs: [{ kind: 'agent', id: 'persona_main' }],
      artifact_ids: ['art-pdf-1'],
      seq: 25,
    },
  ];

  await page.route(`**/api/runs/${runId}/messages*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        messages: mockChatMessages,
        next_cursor: null,
      }),
    });
  });

  await page.route(`**/api/runs/${runId}/chat`, async (route) => {
    const body = route.request().postDataJSON() as { request_id: string; text: string };
    mockChatMessages.push(
      {
        message_id: `u_${mockChatMessages.length}`,
        run_id: runId,
        kind: 'message',
        role: 'user',
        created_at: '2026-11-26T10:02:00Z',
        text: body.text,
        related_refs: [],
        artifact_ids: [],
        seq: 29,
      },
      {
        message_id: `a_${mockChatMessages.length}`,
        run_id: runId,
        kind: 'message',
        role: 'main',
        created_at: '2026-11-26T10:02:01Z',
        text: '**Boss Agent 诊断说明**：该次运行已由用户手动停止，可继续追问或发送 `重新复现`。',
        related_refs: [{ kind: 'agent', id: 'persona_main' }],
        artifact_ids: [],
        seq: 29,
      }
    );
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        command_id: body.request_id,
        status: 'answered',
        message: '该次运行已由用户手动停止，可继续追问或发送重新复现。',
      }),
    });
  });

  await page.route(`**/api/runs/${runId}/artifacts-detail`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          artifact_id: 'art-upload-1',
          run_id: runId,
          kind: 'other',
          filename: '2019-wu.pdf',
          sha256: '00d984695444abcdef1234567890',
          mime_type: 'application/pdf',
          validation_status: 'pending',
          download_url: '/api/artifacts/art-upload-1/download',
        },
        {
          artifact_id: 'art-dossier-1',
          run_id: runId,
          kind: 'dataset',
          filename: 'paper_dossier.json',
          sha256: '01d984695444abcdef1234567890',
          mime_type: 'application/json',
          validation_status: 'passed',
          download_url: '/api/artifacts/art-dossier-1/download',
        },
        {
          artifact_id: 'art-csv-1',
          run_id: runId,
          kind: 'dataset',
          filename: 'round1_results.csv',
          sha256: '11d984695444abcdef1234567890',
          mime_type: 'text/csv',
          validation_status: 'passed',
          reproduction_level: 'S1',
          download_url: '/api/artifacts/art-csv-1/download',
        },
        {
          artifact_id: 'art-script-1',
          run_id: runId,
          kind: 'script',
          filename: 'exp_round1_reproduction.py',
          sha256: '33d984695444abcdef1234567890',
          mime_type: 'text/x-python',
          validation_status: 'passed',
          reproduction_level: 'S1',
          download_url: '/api/artifacts/art-script-1/download',
        },
        {
          artifact_id: 'art-pdf-1',
          run_id: runId,
          kind: 'report',
          filename: 'reproduction_report.pdf',
          sha256: '22d984695444abcdef1234567890',
          mime_type: 'application/pdf',
          validation_status: 'passed',
          reproduction_level: 'S1',
          download_url: '/api/artifacts/art-pdf-1/download',
        },
      ]),
    });
  });

  await page.route(`**/api/runs/${runId}/timeline`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: runId,
        latest_seq: 25,
        snapshots: [
          {
            snapshot_id: 'snap_start_collab_17',
            seq: 0,
            kind: 'huddle_start',
            stage: 'debate',
            duration_sec: 3.0,
            label: 'Huddle Start',
            reason: '## Critique of Round 1\nValidation challenged **window comparability** on `cd_5`.',
            report: '',
            collaboration_id: 'collab-17',
            participants: ['Analysis', 'Validation'],
            linked_step_ids: ['step_debate_collab-17'],
          },
          {
            snapshot_id: 'snap_expand_3p',
            seq: 1,
            kind: 'huddle_expand',
            stage: 'debate',
            duration_sec: 12.5,
            label: '3-Agent Pod',
            reason: 'Data joined Validation & Analysis.',
            report: '',
            collaboration_id: 'collab-17',
            participants: ['Analysis', 'Validation', 'Data'],
            linked_step_ids: ['step_debate_collab-17'],
          },
          {
            snapshot_id: 'snap_expand_5p',
            seq: 2,
            kind: 'huddle_expand',
            stage: 'debate',
            duration_sec: 28.0,
            label: '5-Agent Pod',
            reason: 'Dynamic Analysis #2 & Data #2 joined.',
            report: '',
            collaboration_id: 'collab-17',
            participants: ['Analysis', 'Validation', 'Data', 'Analysis #2', 'Data #2'],
            linked_step_ids: ['step_debate_collab-17'],
          },
          {
            snapshot_id: 'snap_multi_3t',
            seq: 3,
            kind: 'huddle_expand',
            stage: 'intervention',
            duration_sec: 6.0,
            label: 'Concurrent Tables',
            reason: 'Parallel table cap check.',
            report: '',
            collaboration_id: 'collab-17',
            participants: ['Analysis', 'Validation'],
            linked_step_ids: ['step_debate_collab-17'],
          },
          {
            snapshot_id: 'snap_end_collab_17',
            seq: 4,
            kind: 'huddle_end',
            stage: 'synthesis',
            duration_sec: 9.0,
            label: 'Huddle Report',
            reason: 'Validation challenged Round 1 window comparability.',
            report: 'Round 2 ablation and permutation null confirmed main effect holds.',
            collaboration_id: 'collab-17',
            briefing_id: null,
            participants: ['Analysis', 'Validation', 'Data'],
            linked_step_ids: ['step_debate_collab-17'],
          },
          {
            snapshot_id: 'snap_brief_28',
            seq: 5,
            kind: 'briefing',
            stage: 'briefing',
            duration_sec: 5.0,
            label: 'Validation Brief',
            reason: 'Evidence Gate & Robustness Audit',
            report: 'Validation presented S1 PASSED Evidence Gate verdict & Round 2 p=0.0049 summary to Boss.',
            collaboration_id: null,
            briefing_id: 'brief_28',
            participants: ['Validation'],
            linked_step_ids: ['step_debate_collab-17'],
          },
        ],
        briefings: {
          brief_28: {
            briefing_id: 'brief_28',
            seq: 28,
            presenter_ids: ['persona_validation'],
            presenter_labels: ['Validation'],
            topic: 'Evidence Gate & Robustness Audit',
            summary: 'Validation presented S1 PASSED Evidence Gate verdict & Round 2 p=0.0049 summary to Boss.',
            artifact_ids: ['art-script-1'],
            linked_step_ids: ['step_debate_collab-17'],
          },
        },
        active_intervention: {
          intervention_id: 'int-e2e-1',
          seq: 18,
          resolved_seq: null,
          status: 'pending',
          debate_id: 'debate-03',
          collaboration_id: 'collab-17',
          from_persona_ids: ['persona_validation', 'persona_analysis'],
          from_labels: ['Validation', 'Analysis'],
          to_persona_id: 'persona_main',
          dissent_summary: 'Estimator mismatch: $\\text{CI}$ coverage vs permutation $p$-value.',
          question: 'Which estimator should Round 2 lock onto?',
          options: [{ label: 'Audit paired SE', prompt: 'Audit paired SE in Round 2.' }],
          user_decision: null,
          linked_step_ids: ['step_debate_collab-17'],
        },
        table_dialogues: {
          'collab-17': {
            collaboration_id: 'collab-17',
            kind: 'debate',
            topic: 'Citation window comparability',
            participants: ['Analysis', 'Validation', 'Data'],
            start_reason: 'Validation challenged Round 1 window comparability.',
            end_report: 'Round 2 ablation and permutation null confirmed main effect holds.',
            turns: [
              { seq: 1, speaker_id: 'persona_validation', speaker_label: 'Validation', stance: 'challenge', summary: 'Check whether sampling bias or missing controls explain the Round 1 gap.' },
              { seq: 2, speaker_id: 'persona_analysis', speaker_label: 'Analysis', stance: 'propose_test', summary: 'Proposed convening Data and running Round 2 ablations & permutation null.' },
              { seq: 3, speaker_id: 'persona_analysis_2', speaker_label: 'Analysis #2', stance: 'report_round2', summary: 'Ablation dropped metric by 0.12; permutation null $p=0.0049$.' },
              { seq: 4, speaker_id: 'persona_analysis_3', speaker_label: 'Analysis #3', stance: 'stress_test', summary: 'Noise sweep retained $92\\%$ effect stability.' },
              { seq: 5, speaker_id: 'persona_main', speaker_label: 'Boss', stance: 'human_guidance', summary: 'Human guidance relayed by Boss: Audit paired SE in Round 2.' },
            ],
          },
        },
        chat_steps: [
          {
            step_id: 'step_main_init',
            status: 'done',
            actor_label: 'Boss agent',
            text: 'Boss agent read uploaded files and dispatched specialists',
            artifact_ids: [],
            snapshot_seq: 0,
          },
          {
            step_id: 'step_data',
            status: 'done',
            actor_label: 'Data agents',
            text: 'Data agents found dataset schemas & benchmark findings',
            artifact_ids: ['art-dossier-1', 'art-csv-1'],
            snapshot_seq: 0,
          },
          {
            step_id: 'step_debate_collab-17',
            status: 'done',
            actor_label: 'Research Team',
            text: 'Agents debated & ran Round 2 robustness experiments',
            artifact_ids: ['art-script-1'],
            collaboration_id: 'collab-17',
            debate_summary: 'Round 2 ablation and permutation null confirmed main effect holds.',
            snapshot_seq: 0,
          },
          {
            step_id: 'step_main_summary',
            status: 'done',
            actor_label: 'Boss agent',
            text: 'Boss agent summarized findings, final PDF report & reproducible scripts',
            artifact_ids: ['art-pdf-1', 'art-script-1'],
            snapshot_seq: 4,
          },
        ],
      }),
    });
  });

  await page.route('**/api/artifacts/art-pdf-1/preview', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        artifact_id: 'art-pdf-1',
        filename: 'reproduction_report.pdf',
        mime_type: 'application/pdf',
        preview_kind: 'pdf',
        inline_url: '/api/artifacts/art-pdf-1/download?inline=true',
      }),
    });
  });

  await page.route(`**/api/runs/${runId}/events*`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
  });

  await page.goto('/');
  const ws = await page.evaluate(async () => {
    const res = await fetch('/api/workspaces', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Table Morph Test', workspace_type: 'reproduce' }),
    });
    return res.json();
  });
  await page.evaluate((id) => localStorage.setItem('whales_research_canvas_workspace_id', id), ws.workspace_id);
  await page.reload();

  // Prominent SIMULATION badge visible; Reconnecting pill & legacy Replay header button/scrubber removed
  await expect(page.getByTestId('simulation-mode-badge')).toBeVisible();
  await expect(page.locator('.rep-mode-pill.reconnecting')).toHaveCount(0);
  await expect(page.getByTestId('toggle-replay-btn')).toHaveCount(0);
  await expect(page.getByTestId('replay-scrubber')).toHaveCount(0);

  // Step 0 via bottom histogram timeline dock -> taller bars for longer steps + stage color coding + top-right Back button
  await expect(page.getByTestId('canvas-timeline-dock')).toBeVisible();
  const trackScrollbarWidth = await page
    .locator('.rep-timeline-track')
    .evaluate((el) => getComputedStyle(el).scrollbarWidth);
  expect(trackScrollbarWidth).toBe('none');
  await expect(page.locator('.rep-timeline-track .rep-timeline-live-btn')).toHaveCount(0);

  // Histogram bar height reflects segment duration & distinct workflow stage attributes
  const hStartBar = Number(
    await page.getByTestId('timeline-snap-snap_start_collab_17').getAttribute('data-bar-height')
  );
  const hExpand5Bar = Number(
    await page.getByTestId('timeline-snap-snap_expand_5p').getAttribute('data-bar-height')
  );
  expect(hExpand5Bar).toBeGreaterThan(hStartBar);
  await expect(page.getByTestId('timeline-snap-snap_brief_28')).toHaveAttribute('data-stage', 'briefing');
  await expect(page.getByTestId('timeline-snap-snap_end_collab_17')).toHaveAttribute('data-stage', 'synthesis');

  // KaTeX math renders cleanly in Left Chat + Human Intervention Card visible on paused debate
  await expect(page.locator('.rep-chat-sidebar .markdown-body .katex').first()).toBeVisible();
  await expect(page.getByTestId('human-intervention-card')).toBeVisible();

  // User chat bubble renders crisp white text on blue background
  const userBubbleColor = await page
    .locator('.msg-user .markdown-body')
    .first()
    .evaluate((el) => getComputedStyle(el).color);
  expect(userBubbleColor).toBe('rgb(255, 255, 255)');

  // Histogram caps visible bars to 5 with hidden horizontal scrollbar, smooth scrolling & larger click hit targets
  const histMetrics = await page.getByTestId('timeline-histogram').evaluate((el) => ({
    clientWidth: el.clientWidth,
    scrollWidth: el.scrollWidth,
    scrollbarWidth: getComputedStyle(el).scrollbarWidth,
    overscrollBehaviorX: getComputedStyle(el).overscrollBehaviorX,
  }));
  expect(histMetrics.clientWidth).toBeLessThanOrEqual(216);
  expect(histMetrics.scrollWidth).toBeGreaterThan(histMetrics.clientWidth);
  expect(histMetrics.scrollbarWidth).toBe('none');
  expect(histMetrics.overscrollBehaviorX).toBe('contain');
  const firstBarBox = await page.getByTestId('timeline-snap-snap_start_collab_17').boundingBox();
  expect(firstBarBox).not.toBeNull();
  expect(firstBarBox!.width).toBeGreaterThanOrEqual(34);
  expect(firstBarBox!.height).toBeGreaterThanOrEqual(40);

  // Live mode: active table shows a single-line Thinking Bubble, pen-only two-line writing motif inside table, and ZERO caption text below table
  const liveBubble = page.getByTestId('live-table-thinking-bubble-collab-17');
  await expect(liveBubble).toBeVisible();
  await expect(liveBubble.locator('.rep-thinking-pill')).not.toHaveText('');
  const motif = page.getByTestId('table-context-motif-collab-17');
  await expect(motif).toBeVisible();
  await expect(motif.locator('path.rep-table-context-line')).toHaveCount(2);
  await expect(motif.locator('rect')).toHaveCount(0);
  const motifFill = await motif
    .locator('path.rep-table-context-line')
    .first()
    .evaluate((el) => getComputedStyle(el).fill);
  expect(motifFill).toBe('none');
  await expect(page.locator('.rep-table-topic-caption')).toHaveCount(0);

  // Bubble collision invariant: pill & dots do NOT overlap any persona node (including nearby Analysis #2) or the table center pen lines
  const boxesOverlap = (
    a: { x: number; y: number; width: number; height: number },
    b: { x: number; y: number; width: number; height: number }
  ) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;

  const pillBox = await liveBubble.locator('.rep-thinking-pill').boundingBox();
  const motifBox = await motif.boundingBox();
  expect(pillBox).not.toBeNull();
  expect(motifBox).not.toBeNull();
  expect(boxesOverlap(pillBox!, motifBox!)).toBe(false);

  const dotCount = await liveBubble.locator('.rep-thinking-dot').count();
  expect(dotCount).toBe(2);
  for (let d = 0; d < dotCount; d += 1) {
    const dotBox = await liveBubble.locator('.rep-thinking-dot').nth(d).boundingBox();
    expect(dotBox).not.toBeNull();
    expect(boxesOverlap(dotBox!, motifBox!)).toBe(false);
  }

  const personaCountLive = await page.locator('.rep-persona-node').count();
  for (let p = 0; p < personaCountLive; p += 1) {
    const pNode = page.locator('.rep-persona-node').nth(p);
    const pBox = await pNode.boundingBox();
    expect(pBox).not.toBeNull();
    expect(boxesOverlap(pillBox!, pBox!), `Bubble overlaps persona #${p}`).toBe(false);
  }
  const liveWorldCoords = await page.locator('.rep-persona-node').evaluateAll((els) =>
    els.map((el) => ({
      id: el.getAttribute('data-persona-id') || '',
      x: Number(el.getAttribute('data-world-x')),
      y: Number(el.getAttribute('data-world-y')),
    }))
  );
  for (let i = 0; i < liveWorldCoords.length; i += 1) {
    for (let j = i + 1; j < liveWorldCoords.length; j += 1) {
      const dist = Math.hypot(
        liveWorldCoords[i].x - liveWorldCoords[j].x,
        liveWorldCoords[i].y - liveWorldCoords[j].y
      );
      expect(dist, `Live collision between ${liveWorldCoords[i].id} and ${liveWorldCoords[j].id}`).toBeGreaterThanOrEqual(92);
    }
  }

  await page.getByTestId('research-table-collab-17').hover({ force: true });
  await expect(page.getByTestId('table-dialogue-popover')).toHaveCount(0);

  // Clicking table populates concise single-line Context chip without ellipses
  await page.getByTestId('research-table-collab-17').click({ force: true });
  const ctxChipText = ((await page.getByTestId('selected-context-chip').textContent()) || '').trim();
  expect(ctxChipText.startsWith('Context:')).toBe(true);
  expect(ctxChipText.length).toBeLessThanOrEqual(32);
  expect(ctxChipText).not.toMatch(/…|\.\.\./);

  await page.getByTestId('timeline-snap-snap_start_collab_17').click();
  await expect(page.getByTestId('live-table-thinking-bubble-collab-17')).toHaveCount(0);
  await expect(page.getByTestId('replay-mode-badge')).toHaveCount(0);
  await expect(page.getByTestId('timeline-return-live-btn')).toHaveText('Back');
  const bannerText = ((await page.getByTestId('timeline-snapshot-banner').textContent()) || '').trim();
  expect(bannerText).toContain('Why started:');
  expect(bannerText).toContain('Critique of Round 1');
  expect(bannerText).not.toMatch(/##|\*\*|`|…|\.\.\./);
  await expect(page.getByTestId('timeline-step-step_debate_collab-17')).toHaveClass(/is-linked/);

  // Debate dissent Email icon visible on Canvas; hover shows group-chat popover with 0 write POSTs
  const mailIcon = page.getByTestId('intervention-mail-icon');
  await expect(mailIcon).toBeVisible();
  await mailIcon.hover({ force: true });
  await expect(page.getByTestId('intervention-mail-popover')).toBeVisible();
  await expect(page.getByTestId('intervention-mail-popover')).toContainText('Estimator mismatch');

  const tableGroup = page.getByTestId('research-table-collab-17');
  await expect(tableGroup).toHaveAttribute('data-table-shape', 'rectangle');

  await page.getByTestId('research-table-path-collab-17').evaluate((el) => {
    (el as SVGPathElement & { __sameDomRef?: boolean }).__sameDomRef = true;
  });

  // Step 1 -> 3-person circle on SAME <path>
  await page.getByTestId('timeline-snap-snap_expand_3p').click();
  await expect(tableGroup).toHaveAttribute('data-table-shape', 'circle');
  const keptAfter3 = await page
    .getByTestId('research-table-path-collab-17')
    .evaluate((el) => Boolean((el as SVGPathElement & { __sameDomRef?: boolean }).__sameDomRef));
  expect(keptAfter3).toBe(true);

  // Step 2 -> 5-person ellipse on SAME <path> including dynamic specialists
  await page.getByTestId('timeline-snap-snap_expand_5p').click();
  await expect(tableGroup).toHaveAttribute('data-table-shape', 'ellipse');
  const keptAfter5 = await page
    .getByTestId('research-table-path-collab-17')
    .evaluate((el) => Boolean((el as SVGPathElement & { __sameDomRef?: boolean }).__sameDomRef));
  expect(keptAfter5).toBe(true);

  // Dynamically spawned Analysis #2 & Data #2 visible simultaneously alongside base Analysis & Data
  await expect(page.getByTestId('persona-persona_analysis_2')).toBeVisible();
  await expect(page.getByTestId('persona-persona_data_2')).toBeVisible();

  const assertAllAgentsSeparated = async () => {
    const coords = await page.locator('.rep-persona-node').evaluateAll((els) =>
      els.map((el) => ({
        id: el.getAttribute('data-persona-id') || '',
        x: Number(el.getAttribute('data-world-x')),
        y: Number(el.getAttribute('data-world-y')),
      }))
    );
    for (let i = 0; i < coords.length; i += 1) {
      for (let j = i + 1; j < coords.length; j += 1) {
        const dist = Math.hypot(coords[i].x - coords[j].x, coords[i].y - coords[j].y);
        expect(dist, `Collision between ${coords[i].id} and ${coords[j].id}`).toBeGreaterThanOrEqual(92);
      }
    }
  };
  await assertAllAgentsSeparated();

  // 5-person ellipse table collision invariant: NO agent box (including bottom name/status label) enters ellipse interior
  const ellipsePathBox = await page.getByTestId('research-table-path-collab-17').boundingBox();
  expect(ellipsePathBox).not.toBeNull();
  const ecx = ellipsePathBox!.x + ellipsePathBox!.width / 2;
  const ecy = ellipsePathBox!.y + ellipsePathBox!.height / 2;
  const erx = (ellipsePathBox!.width / 2) * 0.95;
  const ery = (ellipsePathBox!.height / 2) * 0.95;
  const pCount5 = await page.locator('.rep-persona-node').count();
  for (let p = 0; p < pCount5; p += 1) {
    const pBox = await page.locator('.rep-persona-node').nth(p).boundingBox();
    expect(pBox).not.toBeNull();
    const qx = Math.max(pBox!.x, Math.min(ecx, pBox!.x + pBox!.width));
    const qy = Math.max(pBox!.y, Math.min(ecy, pBox!.y + pBox!.height));
    const ellipseDist = Math.hypot((qx - ecx) / erx, (qy - ecy) / ery);
    expect(ellipseDist, `Agent #${p} box/label intrudes into 5-person ellipse`).toBeGreaterThanOrEqual(1.0);
  }

  // Fit All adjusts zoom cleanly without errors
  await page.getByTestId('reproduce-fit-all-btn').click();
  await expect(page.getByTestId('reproduce-zoom-scrub')).toContainText('%');

  // Hover discussion table -> larger floating group-chat dialogue popover + Expand to full right Canvas
  const tblEl = page.getByTestId('research-table-collab-17');
  await tblEl.hover({ force: true });
  const pop = page.getByTestId('table-dialogue-popover');
  await expect(pop).toBeVisible();
  await expect(pop).toContainText('Ablation dropped metric by 0.12');
  await expect(pop.locator('[data-speaker-id="persona_validation"]')).toHaveAttribute('data-bubble-side', 'left');
  await expect(pop.locator('[data-speaker-id="persona_main"]')).toHaveAttribute('data-bubble-side', 'right');
  const brightnessByInstance = await pop.locator('.rep-gchat-row').evaluateAll((rows) => {
    const parseRgbLum = (rgbStr: string) => {
      const m = rgbStr.match(/\d+/g)?.map(Number) || [0, 0, 0];
      return m[0] + m[1] + m[2];
    };
    const pick = (id: string) => {
      const el = rows.find((r) => r.getAttribute('data-speaker-id') === id)?.querySelector('.rep-gchat-bubble');
      return el ? parseRgbLum(getComputedStyle(el).backgroundColor) : 0;
    };
    return { a1: pick('persona_analysis'), a2: pick('persona_analysis_2'), a3: pick('persona_analysis_3') };
  });
  expect(brightnessByInstance.a2).toBeGreaterThan(brightnessByInstance.a1);
  expect(brightnessByInstance.a3).toBeGreaterThan(brightnessByInstance.a2);
  await pop.hover();
  await expect(pop).toBeVisible();

  const canvasBox = await page.getByTestId('living-research-canvas').boundingBox();
  const popBox = await pop.boundingBox();
  expect(canvasBox).not.toBeNull();
  expect(popBox).not.toBeNull();
  expect(popBox!.width).toBeGreaterThanOrEqual(420);

  await page.getByTestId('expand-dialogue-popover-btn').click();
  await expect(pop).toHaveClass(/is-expanded/);
  await expect(page.getByTestId('close-dialogue-popover-btn')).toHaveCount(0);
  const expandedBox = await pop.boundingBox();
  expect(expandedBox).not.toBeNull();
  expect(expandedBox!.width).toBeGreaterThanOrEqual(canvasBox!.width - 40);
  expect(expandedBox!.height).toBeGreaterThanOrEqual(canvasBox!.height - 40);

  await page.getByTestId('expand-dialogue-popover-btn').click();
  await expect(pop).not.toHaveClass(/is-expanded/);

  // Pin (pushpin) button sits to the left of Expand button; clicking it pins dialogue onto the Infinite Canvas
  const pinBtn = page.getByTestId('place-dialogue-popover-btn');
  const expandBtn = page.getByTestId('expand-dialogue-popover-btn');
  await expect(pinBtn).toBeVisible();
  await expect(pinBtn).toHaveAttribute('data-icon', 'pin');
  const pinBox = await pinBtn.boundingBox();
  const expandBtnBox = await expandBtn.boundingBox();
  expect(pinBox).not.toBeNull();
  expect(expandBtnBox).not.toBeNull();
  expect(pinBox!.x).toBeLessThan(expandBtnBox!.x);

  await pinBtn.click();
  const placedCard = page.getByTestId('placed-dialogue-card-collab-17');
  await expect(placedCard).toBeVisible();
  await expect(page.locator('.rep-placed-cards-layer [data-testid="placed-dialogue-card-collab-17"]')).toHaveCount(1);
  await expect(page.locator('.rep-composite-group-zone')).toHaveCount(0);
  const layerOrders = await page.evaluate(() => {
    const personasEl = document.querySelector('.rep-personas-dom-layer');
    const cardsEl = document.querySelector('.rep-placed-cards-layer');
    return {
      personasZ: personasEl ? Number(getComputedStyle(personasEl).zIndex || 0) : -1,
      cardsZ: cardsEl ? Number(getComputedStyle(cardsEl).zIndex || 0) : -1,
    };
  });
  expect(layerOrders.cardsZ).toBeGreaterThan(layerOrders.personasZ);
  // Hovering is no longer needed; moving pointer away keeps placed card visible without opening hover popover
  await page.mouse.move(20, 20);
  await expect(placedCard).toBeVisible();
  await expect(page.getByTestId('table-dialogue-popover')).toHaveCount(0);

  // Clicking active Pin unpins it; hovering or clicking a seated member of the table treats Table + Agents as one entity
  const unpinBtn = page.getByTestId('unplace-dialogue-card-btn-collab-17');
  await expect(unpinBtn).toHaveAttribute('data-icon', 'pin');
  await unpinBtn.click();
  await expect(placedCard).toHaveCount(0);

  await page.getByTestId('persona-persona_analysis').hover({ force: true });
  await expect(page.getByTestId('table-dialogue-popover')).toBeVisible();

  // Re-pin dialogue card onto the Infinite Canvas, clear context chip, and click seated member as unified entity
  await page.getByTestId('place-dialogue-popover-btn').click();
  await expect(placedCard).toBeVisible();
  await page.getByRole('button', { name: 'Clear context reference' }).click();
  await expect(page.getByTestId('selected-context-chip')).toHaveCount(0);
  await page.getByTestId('persona-persona_analysis').click();
  await expect(page.getByTestId('selected-context-chip')).toContainText('Table');

  // Pinned card supports free resize on the upper layer WITHOUT colliding with or displacing agents
  const agentCoordsBeforeResize = await page.locator('.rep-persona-node').evaluateAll((els) =>
    els.map((el) => ({
      id: el.getAttribute('data-persona-id'),
      x: Number(el.getAttribute('data-world-x')),
      y: Number(el.getAttribute('data-world-y')),
    }))
  );
  const initialCardBox = await placedCard.boundingBox();
  const resizeGrip = page.getByTestId('resize-placed-card-collab-17');
  expect(initialCardBox).not.toBeNull();
  await expect(resizeGrip).toBeVisible();
  const gripBox = await resizeGrip.boundingBox();
  expect(gripBox).not.toBeNull();
  await page.mouse.move(gripBox!.x + gripBox!.width / 2, gripBox!.y + gripBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(gripBox!.x + gripBox!.width / 2 + 115, gripBox!.y + gripBox!.height / 2 + 85, { steps: 6 });
  await page.mouse.up();
  const resizedCardBox = await placedCard.boundingBox();
  expect(resizedCardBox).not.toBeNull();
  expect(resizedCardBox!.width).toBeGreaterThan(initialCardBox!.width + 60);
  expect(resizedCardBox!.height).toBeGreaterThan(initialCardBox!.height + 45);
  expect(Math.abs(resizedCardBox!.x - initialCardBox!.x)).toBeLessThanOrEqual(4);

  const agentCoordsAfterResize = await page.locator('.rep-persona-node').evaluateAll((els) =>
    els.map((el) => ({
      id: el.getAttribute('data-persona-id'),
      x: Number(el.getAttribute('data-world-x')),
      y: Number(el.getAttribute('data-world-y')),
    }))
  );
  expect(agentCoordsAfterResize).toEqual(agentCoordsBeforeResize);
  await assertAllAgentsSeparated();

  // Timeline drag 1: single agent (persona_paper) can be dragged individually
  const paperEl = page.getByTestId('persona-persona_paper');
  const paperBeforeX = Number(await paperEl.getAttribute('data-world-x'));
  const paperBeforeY = Number(await paperEl.getAttribute('data-world-y'));
  const paperBox = await paperEl.boundingBox();
  expect(paperBox).not.toBeNull();
  await page.mouse.move(paperBox!.x + paperBox!.width / 2, paperBox!.y + paperBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(paperBox!.x + paperBox!.width / 2 - 90, paperBox!.y + paperBox!.height / 2 - 45, { steps: 6 });
  await page.mouse.up();
  const paperAfterX = Number(await paperEl.getAttribute('data-world-x'));
  const paperAfterY = Number(await paperEl.getAttribute('data-world-y'));
  expect(Math.hypot(paperAfterX - paperBeforeX, paperAfterY - paperBeforeY)).toBeGreaterThan(25);

  // Timeline drag 2: dragging a seated member inside a cluster drags the ENTIRE cluster rigidly (table + all 5 members)
  const podMembers = ['persona_analysis', 'persona_validation', 'persona_data', 'persona_analysis_2', 'persona_data_2'];
  const getPodSnapshot = async () => {
    const members = await Promise.all(
      podMembers.map(async (pid) => {
        const el = page.getByTestId(`persona-${pid}`);
        return {
          pid,
          x: Number(await el.getAttribute('data-world-x')),
          y: Number(await el.getAttribute('data-world-y')),
        };
      })
    );
    const tblBox = await page.getByTestId('research-table-path-collab-17').boundingBox();
    return { members, tblBox };
  };
  const podBefore = await getPodSnapshot();
  // Pick a cluster member whose center is not covered by the upper-layer pinned card
  const finalCardBox = await placedCard.boundingBox();
  let dragHandleBox: { x: number; y: number; width: number; height: number } | null = null;
  for (const pid of podMembers) {
    const box = await page.getByTestId(`persona-${pid}`).boundingBox();
    if (!box) continue;
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    const occluded =
      finalCardBox &&
      cx >= finalCardBox.x - 4 &&
      cx <= finalCardBox.x + finalCardBox.width + 4 &&
      cy >= finalCardBox.y - 4 &&
      cy <= finalCardBox.y + finalCardBox.height + 4;
    if (!occluded) {
      dragHandleBox = box;
      break;
    }
  }
  expect(dragHandleBox).not.toBeNull();
  await page.mouse.move(dragHandleBox!.x + dragHandleBox!.width / 2, dragHandleBox!.y + dragHandleBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(dragHandleBox!.x + dragHandleBox!.width / 2 - 75, dragHandleBox!.y + dragHandleBox!.height / 2 + 55, { steps: 6 });
  await page.mouse.up();
  const podAfter = await getPodSnapshot();
  const deltaAnalysisX = podAfter.members[0].x - podBefore.members[0].x;
  const deltaAnalysisY = podAfter.members[0].y - podBefore.members[0].y;
  expect(Math.hypot(deltaAnalysisX, deltaAnalysisY)).toBeGreaterThan(20);
  for (let m = 1; m < podMembers.length; m += 1) {
    const dx = podAfter.members[m].x - podBefore.members[m].x;
    const dy = podAfter.members[m].y - podBefore.members[m].y;
    expect(Math.abs(dx - deltaAnalysisX), `${podMembers[m]} dx matched cluster`).toBeLessThanOrEqual(4);
    expect(Math.abs(dy - deltaAnalysisY), `${podMembers[m]} dy matched cluster`).toBeLessThanOrEqual(4);
  }
  expect(Math.hypot(podAfter.tblBox!.x - podBefore.tblBox!.x, podAfter.tblBox!.y - podBefore.tblBox!.y)).toBeGreaterThan(15);
  const paperBeforeSwitchX = Number(await paperEl.getAttribute('data-world-x'));

  // Snapshot drag history persists per timeline snapshot: switch away and back -> restores pinned resized card & dragged positions
  await expect(page.getByTestId('timeline-snap-snap_expand_5p')).toHaveAttribute('data-drag-count', '6');
  await expect(page.getByTestId('timeline-undo-drag-btn')).toBeVisible();
  await page.getByTestId('timeline-snap-snap_end_collab_17').click();
  await expect(page.getByTestId('placed-dialogue-card-collab-17')).toHaveCount(0);
  await page.getByTestId('timeline-snap-snap_expand_5p').click();
  await expect(page.getByTestId('placed-dialogue-card-collab-17')).toBeVisible();
  const restoredCardBox = await placedCard.boundingBox();
  expect(restoredCardBox!.width).toBeGreaterThan(initialCardBox!.width + 60);
  expect(Number(await page.getByTestId('persona-persona_paper').getAttribute('data-world-x'))).toBe(paperBeforeSwitchX);
  expect(Number(await page.getByTestId('persona-persona_analysis').getAttribute('data-world-x'))).toBe(podAfter.members[0].x);

  // Undo restores previous cluster position cleanly
  await page.getByTestId('timeline-undo-drag-btn').click();
  expect(Number(await page.getByTestId('persona-persona_analysis').getAttribute('data-world-x'))).toBe(podBefore.members[0].x);
  await expect(page.getByTestId('timeline-snap-snap_expand_5p')).toHaveAttribute('data-drag-count', '5');

  // Step 3: 3 simultaneous collaborations -> at most 2 visible non-overlapping tables, all 9 personas rendered
  await page.getByTestId('timeline-snap-snap_multi_3t').click();
  await expect(page.locator('.rep-table-group')).toHaveCount(2);
  await expect(page.locator('.rep-persona-node')).toHaveCount(9);
  const t0Box = await page.locator('.rep-table-outline').nth(0).boundingBox();
  const t1Box = await page.locator('.rep-table-outline').nth(1).boundingBox();
  expect(t0Box).not.toBeNull();
  expect(t1Box).not.toBeNull();
  expect(boxesOverlap(t0Box!, t1Box!)).toBe(false);

  // Step 4: Ended -> 0 tables, all 9 personas spread cleanly with non-overlapping coordinates
  await page.getByTestId('timeline-snap-snap_end_collab_17').click();
  await expect(page.locator('.rep-table-group')).toHaveCount(0);
  await expect(page.locator('.rep-persona-node')).toHaveCount(9);

  const ax = Number(await page.getByTestId('persona-persona_analysis').getAttribute('data-world-x'));
  const ay = Number(await page.getByTestId('persona-persona_analysis').getAttribute('data-world-y'));
  const a2x = Number(await page.getByTestId('persona-persona_analysis_2').getAttribute('data-world-x'));
  const a2y = Number(await page.getByTestId('persona-persona_analysis_2').getAttribute('data-world-y'));
  expect(Math.hypot(a2x - ax, a2y - ay)).toBeGreaterThan(75);
  await assertAllAgentsSeparated();

  // Step 5: Main <-> Subagent PPT Whiteboard Easel briefing interaction
  const writeRequestsDuringBriefing: string[] = [];
  page.on('request', (req) => {
    if (req.method() !== 'GET' && req.url().includes('/api/')) {
      writeRequestsDuringBriefing.push(`${req.method()} ${req.url()}`);
    }
  });
  await page.getByTestId('timeline-snap-snap_brief_28').click();
  const easelEl = page.getByTestId('briefing-easel');
  await expect(easelEl).toBeVisible();
  const mainXAtBrief = Number(await page.getByTestId('persona-persona_main').getAttribute('data-world-x'));
  const valXAtBrief = Number(await page.getByTestId('persona-persona_validation').getAttribute('data-world-x'));
  expect(mainXAtBrief).toBeLessThanOrEqual(200);
  expect(valXAtBrief).toBeGreaterThan(300);
  expect(valXAtBrief).toBeLessThan(400);
  await assertAllAgentsSeparated();

  await easelEl.hover({ force: true });
  const briefPop = page.getByTestId('briefing-easel-popover');
  await expect(briefPop).toBeVisible();
  await expect(briefPop).toContainText('Evidence Gate & Robustness Audit');
  await expect(briefPop).toContainText('Validation presented S1 PASSED Evidence Gate verdict');
  await expect(page.getByTestId('place-briefing-popover-btn')).toHaveAttribute('data-icon', 'pin');
  await briefPop.hover();
  await expect(briefPop).toBeVisible();

  await easelEl.click({ force: true });
  expect(writeRequestsDuringBriefing).toEqual([]);

  // Composite PPT Whiteboard + Agent Group interaction: hovering presenter opens briefing popover; dragging presenter moves Main + Validation + Easel together
  await page.mouse.move(20, 20);
  await expect(briefPop).toHaveCount(0);
  await page.getByTestId('persona-persona_validation').hover({ force: true });
  await expect(briefPop).toBeVisible();
  const briefBoxBefore = await easelEl.boundingBox();
  const valBriefBox = await page.getByTestId('persona-persona_validation').boundingBox();
  expect(briefBoxBefore).not.toBeNull();
  expect(valBriefBox).not.toBeNull();
  await page.mouse.move(valBriefBox!.x + valBriefBox!.width / 2, valBriefBox!.y + valBriefBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(valBriefBox!.x + valBriefBox!.width / 2 + 70, valBriefBox!.y + valBriefBox!.height / 2 + 45, { steps: 6 });
  await page.mouse.up();
  const mainAfterBriefDrag = Number(await page.getByTestId('persona-persona_main').getAttribute('data-world-x'));
  const valAfterBriefDrag = Number(await page.getByTestId('persona-persona_validation').getAttribute('data-world-x'));
  const briefBoxAfter = await easelEl.boundingBox();
  expect(mainAfterBriefDrag - mainXAtBrief).toBeGreaterThan(25);
  expect(Math.abs((valAfterBriefDrag - valXAtBrief) - (mainAfterBriefDrag - mainXAtBrief))).toBeLessThanOrEqual(4);
  expect(briefBoxAfter!.x - briefBoxBefore!.x).toBeGreaterThan(20);

  // Left-aligned vertical spine renders with green `N files` pill expanding inline + final PDF & scripts
  await expect(page.getByTestId('chat-vertical-timeline')).toBeVisible();
  const dataFilesPill = page.getByTestId('step-files-pill-step_data');
  await expect(dataFilesPill).toContainText('2 files');
  await dataFilesPill.click();
  await expect(page.getByTestId('step-files-tray-step_data')).toContainText('round1_results.csv');
  await expect(page.getByTestId('final-scripts-summary')).toContainText('1 script');

  await page.getByTestId('timeline-return-live-btn').click();

  // Follow-up question to Boss Agent appends chronologically at bottom BELOW deliverables
  const chatInput = page.getByRole('textbox', { name: 'Research command or question for Boss Agent' });
  await chatInput.fill('为什么失败了');
  await page.getByRole('button', { name: 'Send command to Boss Agent' }).click();
  const followUser = page.getByTestId('followup-msg-user');
  const followMain = page.getByTestId('followup-msg-main');
  await expect(followUser).toContainText('为什么失败了');
  await expect(followMain).toContainText('Boss Agent 诊断说明');
  const artifactCardsBox = await page.getByTestId('chat-artifact-cards').boundingBox();
  const followUserBox = await followUser.boundingBox();
  const followMainBox = await followMain.boundingBox();
  expect(artifactCardsBox).not.toBeNull();
  expect(followUserBox).not.toBeNull();
  expect(followMainBox).not.toBeNull();
  expect(followUserBox!.y).toBeGreaterThan(artifactCardsBox!.y + artifactCardsBox!.height - 2);
  expect(followMainBox!.y).toBeGreaterThan(followUserBox!.y);

  // Preview final PDF report cleanly inside Left Chat
  await page.getByTestId('preview-artifact-art-pdf-1').click();
  await expect(page.getByTestId('inchat-document-viewer')).toBeVisible();
  await expect(page.getByTestId('inchat-document-viewer')).toContainText('reproduction_report.pdf');
  await expect(page.getByTestId('living-research-canvas').locator('table')).toHaveCount(0);
});

test('PDF-UPLOAD: Homepage PDF upload succeeds via multipart FormData, enables Run, and imports into Reproduce run', async ({
  page,
}) => {
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.reload();

  // Switch to Upload Papers to Reproduce homepage
  await page.getByTestId('landing-mode-switch').click();
  await expect(page.locator('h1.landing-title')).toContainText('Upload Papers to');

  // Attach a PDF directly on the hidden file input
  const pdfPayload = Buffer.from('%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n', 'utf-8');
  await page.locator('input[aria-label="Attach files"]').setInputFiles({
    name: 'sample_research_paper.pdf',
    mimeType: 'application/pdf',
    buffer: pdfPayload,
  });

  // Pill appears, zero "No readable files selected" error, Run button becomes enabled even with empty textarea
  await expect(page.locator('.attach-pill')).toContainText('sample_research_paper.pdf');
  await expect(page.locator('.attach-error')).toHaveCount(0);
  const runBtn = page.locator('button.landing-submit-btn');
  await expect(runBtn).toBeEnabled();

  // Verify import-upload call fires when clicking Run (stub /chat so we don't pay live LLM tokens during UI E2E)
  let importedUploadId = '';
  let capturedAttachmentIds: string[] = [];
  await page.route('**/api/runs/*/import-upload/*', async (route) => {
    const url = route.request().url();
    importedUploadId = url.split('/import-upload/')[1] || '';
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          artifact_id: 'art_pdf_e2e',
          sha256: 'a'.repeat(64),
          mime_type: 'application/pdf',
          size_bytes: pdfPayload.length,
          filename: 'sample_research_paper.pdf',
        },
      ]),
    });
  });
  await page.route('**/api/runs/*/chat', async (route) => {
    const body = route.request().postDataJSON() as { attachment_ids?: string[] };
    capturedAttachmentIds = body?.attachment_ids || [];
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        command_id: 'cmd-e2e',
        status: 'accepted',
        message: 'Accepted PDF',
      }),
    });
  });

  await runBtn.click();
  await expect(page.getByTestId('living-research-canvas')).toBeVisible();
  await expect.poll(() => importedUploadId).toMatch(/^up_/);
  await expect.poll(() => capturedAttachmentIds).toEqual(['art_pdf_e2e']);
});
