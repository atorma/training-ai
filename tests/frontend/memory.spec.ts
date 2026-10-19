import { expect, test } from "@playwright/test";
import type { MemoryEntry, MemoryPage } from "../../src/frontend/api";

test("capture and use shared memory, inspect its source and correct across devices without losing drafts", async ({
  page,
  browser,
}) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.fill("Remember synthetic ankle soreness");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const remembered = page.getByRole("button", {
    name: "Remembered: Synthetic ankle soreness",
    exact: true,
  });
  await expect(remembered).toBeVisible();
  const sourceUrl = page.url();
  await expect(
    page.getByText("Saved conversation: Remember synthetic ankle soreness", {
      exact: true,
    }),
  ).toBeVisible();
  // The notice floats over the transcript and disappears after five seconds.
  const transcript = page.locator(".transcript");
  const withNotice = await transcript.boundingBox();
  await expect(remembered).toBeHidden({ timeout: 7000 });
  expect(await transcript.boundingBox()).toEqual(withNotice);
  await page
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  await composer.fill("Use shared memory today");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText(/Shared context:.*Synthetic ankle soreness/),
  ).toBeVisible();
  await composer.fill("Unsent draft survives memory view");
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  const memory = page.getByRole("dialog", { name: "Memory", exact: true });
  await memory
    .getByRole("button", { name: /Synthetic ankle soreness observation/ })
    .first()
    .click();
  await expect(
    memory.getByRole("textbox", { name: "Statement", exact: true }),
  ).toHaveValue("Synthetic ankle soreness");
  await memory
    .getByRole("textbox", { name: "Statement", exact: true })
    .fill("Corrected synthetic observation");
  await memory.getByRole("button", { name: "Save correction" }).click();
  await expect(
    memory.getByText("Memory updated.", { exact: true }),
  ).toBeVisible();
  await memory.getByRole("button", { name: "Close memory" }).click();
  await expect(composer).toHaveValue("Unsent draft survives memory view");

  const device = await browser.newContext();
  const other = await device.newPage();
  await other.goto(sourceUrl);
  await other.getByRole("button", { name: "Memory", exact: true }).click();
  const remote = other.getByRole("dialog", { name: "Memory", exact: true });
  await remote
    .getByRole("button", {
      name: /Corrected synthetic observation observation/,
    })
    .first()
    .click();
  await remote
    .getByRole("textbox", { name: "Statement", exact: true })
    .fill("Remote synthetic correction");
  await remote.getByRole("button", { name: "Save correction" }).click();
  await expect(
    remote.getByText("Memory updated.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  await expect(
    memory
      .getByRole("button", { name: /Remote synthetic correction observation/ })
      .first(),
  ).toBeVisible();
  await memory
    .getByRole("button", { name: /Remote synthetic correction observation/ })
    .first()
    .click();
  await memory
    .getByRole("button", { name: "Open source conversation" })
    .click();
  await expect(page).toHaveURL(sourceUrl);
  await expect(
    page.getByText("Saved conversation: Remember synthetic ankle soreness", {
      exact: true,
    }),
  ).toBeVisible();
  await device.close();
});

test("mobile memory can end and forget a constraint; concurrent correction preserves a dirty edit", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "Message" })
    .fill("Remember synthetic coach restriction");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page
    .getByRole("button", {
      name: "Remembered: Synthetic coach restriction",
      exact: true,
    })
    .click();
  const memory = page.getByRole("dialog", { name: "Memory", exact: true });
  await memory
    .getByRole("textbox", { name: "Statement", exact: true })
    .fill("Local unsaved correction");
  const list: MemoryPage = await (
    await page.request.get("/api/memory?kind=constraint")
  ).json();
  const entry = list.entries.find(
    (e) => e.statement === "Synthetic coach restriction",
  )!;
  await page.request.patch(`/api/memory/${entry.id}`, {
    data: {
      expected_revision: entry.revision,
      data: { ...entry.data, statement: "Another device's correction" },
    },
  });
  await memory.getByRole("button", { name: "Save correction" }).click();
  await expect(memory.getByRole("alert")).toContainText(
    "changed on another device",
  );
  await expect(
    memory.getByRole("textbox", { name: "Statement", exact: true }),
  ).toHaveValue("Local unsaved correction");
  await memory.getByRole("button", { name: "Reload entry" }).click();
  await expect(
    memory.getByRole("textbox", { name: "Statement", exact: true }),
  ).toHaveValue("Another device's correction");
  const endConstraint = memory.getByRole("button", {
    name: "End constraint",
    exact: true,
  });
  // Feedback stays on screen although the action is at the bottom of the form.
  await endConstraint.click();
  await expect(
    memory.getByText("Choose an end date before ending this constraint.", {
      exact: true,
    }),
  ).toBeInViewport();
  await memory.getByLabel("Ended on", { exact: true }).fill("2026-10-11");
  await endConstraint.click();
  await expect(
    memory.getByText("Constraint ended.", { exact: true }),
  ).toBeInViewport();
  const ended: MemoryEntry = await (
    await page.request.get(`/api/memory/${entry.id}`)
  ).json();
  expect(ended.status).toBe("ended");
  page.once("dialog", (dialog) => dialog.accept());
  await memory
    .getByRole("button", { name: "Forget memory", exact: true })
    .click();
  await expect(
    memory.getByText("Memory forgotten. Original chat messages remain.", {
      exact: true,
    }),
  ).toBeVisible();
  expect(
    (await (await page.request.get(`/api/memory/${entry.id}`)).json()).status,
  ).toBe("forgotten");
});

test("goal capture, horizon correction, closure, resumption and kind correction preserve history", async ({
  page,
}) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.fill("Remember synthetic swimming goal");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText("Saved conversation: Remember synthetic swimming goal", {
      exact: true,
    }),
  ).toBeVisible();
  const sourceUrl = page.url();
  await page
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  await composer.fill("Use shared memory today");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText(/Shared context:.*Synthetic swimming goal/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  await page
    .getByRole("dialog", { name: "Memory", exact: true })
    .getByRole("button", { name: /Synthetic swimming goal goal/ })
    .click();
  const memory = page.getByRole("dialog", { name: "Memory", exact: true });
  await memory.getByLabel("Effective from", { exact: true }).fill("2020-01-01");
  await memory
    .getByLabel("Effective through", { exact: true })
    .fill("2020-02-11");
  await memory
    .getByRole("combobox", { name: "Date precision", exact: true })
    .selectOption("exact");
  await memory.getByRole("button", { name: "Save correction" }).click();
  await expect(memory.getByText(/Revision 2 · needs_review/)).toBeVisible();
  await memory.getByLabel("Closure date", { exact: true }).fill("2020-02-11");
  await memory.getByRole("button", { name: "Achieved", exact: true }).click();
  await expect(memory.getByText(/Revision 3 · achieved/)).toBeVisible();
  await memory
    .getByRole("button", { name: "Resume goal", exact: true })
    .click();
  await expect(memory.getByText(/Revision 4 · needs_review/)).toBeVisible();
  await memory
    .getByRole("combobox", { name: "Memory kind", exact: true })
    .selectOption("observation");
  await memory.getByRole("button", { name: "Save correction" }).click();
  await expect(memory.getByText(/Revision 5 · valid/)).toBeVisible();
  await memory.getByText("Revision history", { exact: true }).click();
  await expect(
    memory.getByText(/goal · Synthetic swimming goal/).first(),
  ).toBeVisible();
  await expect(
    memory.getByText(/observation · Synthetic swimming goal/).first(),
  ).toBeVisible();
  await memory.getByRole("button", { name: "Close memory" }).click();
  await page.goto(sourceUrl);
  await expect(
    page.getByText("Saved conversation: Remember synthetic swimming goal", {
      exact: true,
    }),
  ).toBeVisible();
});

test("manual forgetting lets an active answer finish and preserves unrelated continuation", async ({
  page,
}) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.fill("Remember synthetic coach restriction");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByRole("button", {
      name: "Remembered: Synthetic coach restriction",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Generating response… You can return later.", {
      exact: true,
    }),
  ).toBeVisible();
  const threadId = new URL(page.url()).searchParams.get("thread")!;
  const detail = await (
    await page.request.get(`/api/threads/${threadId}`)
  ).json();
  const receipt = detail.memory_receipts[0];
  expect(
    (
      await page.request.delete(`/api/memory/${receipt.entry_id}`, {
        data: { expected_revision: receipt.entry.revision },
      })
    ).ok(),
  ).toBeTruthy();
  await expect(
    page.getByText("Saved conversation: Remember synthetic coach restriction", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Memory forgotten", { exact: true }),
  ).toBeVisible();
  await composer.fill("Continue explaining bicycle gearing");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText(
      "Saved conversation: Remember synthetic coach restriction | Continue explaining bicycle gearing",
      { exact: true },
    ),
  ).toBeVisible();
});

test("memory notices appear only for this browser's own responses", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "Message" })
    .fill("Remember synthetic ankle soreness");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByRole("button", {
      name: "Remembered: Synthetic ankle soreness",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Saved conversation: Remember synthetic ankle soreness", {
      exact: true,
    }),
  ).toBeVisible();
  const threadId = new URL(page.url()).searchParams.get("thread")!;
  // Another device records memory in the same open conversation.
  const other = await page.request.post(`/api/threads/${threadId}/messages`, {
    data: { text: "Remember synthetic swimming goal" },
  });
  expect(other.status()).toBe(202);
  await expect(
    page.getByText(/Saved conversation: .*Remember synthetic swimming goal$/),
  ).toBeVisible();
  await expect(
    page.getByText("Remembered: Synthetic swimming goal"),
  ).toHaveCount(0);
  // Opening the conversation again replays no earlier changes.
  await page.reload();
  await expect(
    page.getByText(/Saved conversation: .*Remember synthetic swimming goal$/),
  ).toBeVisible();
  await expect(page.getByText(/^Remembered:/)).toHaveCount(0);
});
