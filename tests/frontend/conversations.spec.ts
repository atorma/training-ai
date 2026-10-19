import { expect, test } from "@playwright/test";
import type { Detail, Thread } from "../../src/frontend/api";

function userPrompts(detail: Detail) {
  return detail.messages.flatMap((message) =>
    message.payload.parts
      .filter((part) => part.part_kind === "user-prompt")
      .map((part) => part.content),
  );
}

test("Markdown tables render in chat and stay within narrow screens", async ({
  page,
}) => {
  const markdown = `## Suggested weekly plan

| Day | Workout | Purpose |
|---|---|---|
| **Mon Oct 12** | Easy bike commutes **2 × 25 min** + moderate strength training **45–50 min** | Build aerobic fitness and maintain strength. Keep the effort comfortable throughout both sessions. |
| **Tue Oct 13** | Swimming **50 min** | Focus on technique and easy aerobic swimming. Include relaxed drills with plenty of recovery between sets. |
| **Wed Oct 14** | Easy run **20 min** + easy cycling **40–45 min**, preferably in separate sessions | Add variety to the week while keeping both sessions at a conversational pace. |

**Weekly total approximately 8 h 25 min–9 h**.

| Workout | Minutes |
|:---|---:|
| Swimming | 50 |`;
  await page.goto("/");
  await page.getByRole("textbox", { name: "Message" }).fill(markdown);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const assistant = page.locator(".message").nth(1);
  await expect(assistant.getByRole("table")).toHaveCount(2);
  await expect(page.locator(".message time")).toHaveCount(2);

  // The real test model echoes the prompt; verify both message roles and persistence.
  await page.reload();
  for (const index of [0, 1]) {
    const message = page.locator(".message").nth(index);
    const table = message.getByRole("table").first();
    await expect(table.getByRole("columnheader")).toHaveText([
      "Day",
      "Workout",
      "Purpose",
    ]);
    await expect(table.getByRole("row")).toHaveCount(4);
    await expect(table.locator("strong").first()).toHaveText("Mon Oct 12");
    await expect(table.getByRole("cell").nth(1)).toContainText("2 × 25 min");
    await expect(
      message.getByRole("table").last().getByRole("cell").last(),
    ).toHaveCSS("text-align", "right");
    await expect(
      message.getByText("Weekly total approximately 8 h 25 min–9 h", {
        exact: true,
      }),
    ).toBeVisible();
  }

  for (const width of [1280, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const region = assistant
      .getByRole("region", { name: "Message table" })
      .first();
    const dimensions = await region.evaluate((element) => ({
      client: element.clientWidth,
      scroll: element.scrollWidth,
      transcriptClient: element.closest(".transcript")!.clientWidth,
      transcriptScroll: element.closest(".transcript")!.scrollWidth,
      pageClient: document.documentElement.clientWidth,
      pageScroll: document.documentElement.scrollWidth,
    }));
    expect(dimensions.transcriptScroll).toBeLessThanOrEqual(
      dimensions.transcriptClient,
    );
    expect(dimensions.pageScroll).toBeLessThanOrEqual(dimensions.pageClient);
    if (width < 650) {
      expect(dimensions.scroll).toBeGreaterThan(dimensions.client);
      await region.evaluate((element) => {
        element.scrollLeft = 100;
      });
      expect(
        await region.evaluate((element) => element.scrollLeft),
      ).toBeGreaterThan(0);
    }
  }
});

test("Enter adds a new line instead of sending", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.fill("First line");
  await composer.press("Enter");
  await page.keyboard.type("Second line");
  await expect(composer).toHaveValue("First line\nSecond line");
  await expect(page.locator(".message")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeEnabled();
});

test("composer keeps focus after sending", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "Message" });
  const send = page.getByRole("button", { name: "Send", exact: true });
  for (const prompt of ["First focus question", "Another focus question"]) {
    await composer.fill(prompt);
    await send.click();
    await expect(
      page.getByText("Generating response… You can return later."),
    ).toBeVisible();
    await expect(composer).toBeFocused();
    await expect(composer).toHaveValue("");
    await page.keyboard.type("Draft for the next message");
    await expect(composer).toHaveValue("Draft for the next message");
    await expect(send).toBeDisabled();
    await expect(page.locator(".message").last()).toContainText(prompt);
    await expect(send).toBeEnabled();
    await expect(composer).toBeFocused();
    await expect(composer).toHaveValue("Draft for the next message");
  }
});

test("saved message timestamps persist across browsers and use each browser's time zone", async ({
  browser,
  baseURL,
}) => {
  const helsinki = await browser.newContext({
    baseURL,
    locale: "en-GB",
    timezoneId: "Europe/Helsinki",
  });
  const first = await helsinki.newPage();
  await first.goto("/");
  await first
    .getByRole("textbox", { name: "Message" })
    .fill("Show saved timestamps");
  await first.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    first.getByText("Saved conversation: Show saved timestamps", {
      exact: true,
    }),
  ).toBeVisible();
  const threadURL = first.url();
  const id = new URL(threadURL).searchParams.get("thread");
  const detail: Detail = await (
    await first.request.get(`/api/threads/${id}`)
  ).json();
  const timestamps = detail.messages.map((message) => {
    const saved =
      message.payload.timestamp ??
      message.payload.parts.find((part) => part.timestamp)?.timestamp;
    expect(saved).toBeTruthy();
    return new Date(saved!).toISOString();
  });
  const firstTimes = first.locator(".message time");
  await expect(firstTimes).toHaveCount(2);
  for (const [index, timestamp] of timestamps.entries()) {
    await expect(firstTimes.nth(index)).toHaveAttribute("datetime", timestamp);
    await expect(firstTimes.nth(index)).toHaveText(
      new Intl.DateTimeFormat("en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "Europe/Helsinki",
      }).format(new Date(timestamp)),
    );
  }

  const newYork = await browser.newContext({
    locale: "en-GB",
    timezoneId: "America/New_York",
  });
  const second = await newYork.newPage();
  await second.goto(threadURL);
  const secondTimes = second.locator(".message time");
  await expect(secondTimes).toHaveCount(2);
  for (const [index, timestamp] of timestamps.entries()) {
    await expect(secondTimes.nth(index)).toHaveAttribute("datetime", timestamp);
    await expect(secondTimes.nth(index)).toHaveText(
      new Intl.DateTimeFormat("en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "America/New_York",
      }).format(new Date(timestamp)),
    );
  }
  await second.reload();
  await expect(secondTimes.first()).toHaveAttribute("datetime", timestamps[0]);
  await helsinki.close();
  await newYork.close();
});

test("save, reopen from another browser, continue, rename, and delete", async ({
  page,
  browser,
}) => {
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "Message" })
    .fill("My first question");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText("Saved conversation: My first question", { exact: true }),
  ).toBeVisible();
  const threadURL = page.url();

  const otherDevice = await browser.newContext();
  const otherPage = await otherDevice.newPage();
  await otherPage.goto(threadURL);
  await expect(
    otherPage.getByText("Saved conversation: My first question", {
      exact: true,
    }),
  ).toBeVisible();
  await otherPage
    .getByRole("textbox", { name: "Message" })
    .fill("Continue on another device");
  await otherPage.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    otherPage.getByText("Generating response… You can return later."),
  ).toBeVisible();
  await otherPage.close();

  await expect(
    page.getByText(
      "Saved conversation: My first question | Continue on another device",
      { exact: true },
    ),
  ).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept("My training thread"));
  await page.getByRole("button", { name: "Rename", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "My training thread" }),
  ).toBeVisible();

  await page
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  await page.getByRole("textbox", { name: "Message" }).fill("Separate topic");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText("Saved conversation: Separate topic", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Continue on another device", { exact: true }),
  ).not.toBeVisible();

  await page
    .getByRole("button", { name: "My training thread", exact: true })
    .click();
  await expect(
    page.getByText(
      "Saved conversation: My first question | Continue on another device",
      { exact: true },
    ),
  ).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "My training thread", exact: true }),
  ).toHaveCount(0);
  await otherDevice.close();
});

test("a competing device's response rejects a send without losing the draft", async ({
  page,
}) => {
  const response = await page.request.post("/api/threads");
  const thread: Thread = await response.json();
  const endpoint = `/api/threads/${thread.id}`;
  await page.goto(`/?thread=${thread.id}`);
  const composer = page.getByRole("textbox", { name: "Message" });
  await expect(composer).toBeEnabled();
  const draft = "Keep my rejected draft";
  await composer.fill(draft);

  // Start a real competing run after the browser's stale idle check, before its POST.
  await page.route(`**${endpoint}/messages`, async (route) => {
    const competing = await page.request.post(`${endpoint}/messages`, {
      data: { text: "Another device's question" },
    });
    expect(competing.status()).toBe(202);
    await route.continue();
  });
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("already running");
  await expect(composer).toHaveValue(draft);
  const rejected: Detail = await (await page.request.get(endpoint)).json();
  expect(userPrompts(rejected)).toEqual(["Another device's question"]);
  await expect(
    page.locator(".message").getByText(draft, { exact: true }),
  ).toHaveCount(0);

  await page.unroute(`**${endpoint}/messages`);
  await expect(
    page.getByText("Saved conversation: Another device's question", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText(`Saved conversation: Another device's question | ${draft}`, {
      exact: true,
    }),
  ).toBeVisible();
  const saved: Detail = await (await page.request.get(endpoint)).json();
  expect(userPrompts(saved).filter((text) => text === draft)).toHaveLength(1);
});

test("a first message survives a network failure and can be retried in the same thread", async ({
  page,
}) => {
  const before: Thread[] = await (
    await page.request.get("/api/threads")
  ).json();
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "Message" });
  await composer.fill("Recover my first message");
  await page.route("**/api/threads/*/messages", (route) =>
    route.abort("failed"),
  );
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(composer).toHaveValue("Recover my first message");
  const after: Thread[] = await (await page.request.get("/api/threads")).json();
  const created = after.filter(
    (thread) => !before.some((old) => old.id === thread.id),
  );
  expect(created).toHaveLength(1);
  const detail: Detail = await (
    await page.request.get(`/api/threads/${created[0].id}`)
  ).json();
  expect(detail.messages).toEqual([]);

  await page.unroute("**/api/threads/*/messages");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText("Saved conversation: Recover my first message", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`thread=${created[0].id}$`));
  const final: Thread[] = await (await page.request.get("/api/threads")).json();
  expect(final).toHaveLength(before.length + 1);
});

test("a refresh failure after acceptance does not restore an already-saved message", async ({
  page,
}) => {
  const response = await page.request.post("/api/threads");
  const thread: Thread = await response.json();
  const endpoint = `/api/threads/${thread.id}`;
  await page.goto(`/?thread=${thread.id}`);
  const composer = page.getByRole("textbox", { name: "Message" });
  await expect(composer).toBeEnabled();
  await composer.fill("Accepted before refresh failed");
  await page.route(`**${endpoint}`, (route) => route.abort("failed"));
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(composer).toHaveValue("");
  const saved: Detail = await (await page.request.get(endpoint)).json();
  expect(userPrompts(saved)).toEqual(["Accepted before refresh failed"]);
  await page.unroute(`**${endpoint}`);
  await expect(
    page.getByText("Saved conversation: Accepted before refresh failed", {
      exact: true,
    }),
  ).toBeVisible();
});

test("cross-device deletion clears the selection and keeps the sidebar updating", async ({
  page,
  browser,
}) => {
  const response = await page.request.post("/api/threads");
  const thread: Thread = await response.json();
  await page.request.patch(`/api/threads/${thread.id}`, {
    data: { title: "Deleted elsewhere" },
  });
  await page.goto(`/?thread=${thread.id}`);
  await expect(
    page.getByRole("heading", { name: "Deleted elsewhere" }),
  ).toBeVisible();
  const device = await browser.newContext();
  const other = await device.newPage();
  await other.goto(`/?thread=${thread.id}`);
  await expect(
    other.getByRole("button", { name: "Delete", exact: true }),
  ).toBeEnabled();
  other.once("dialog", (dialog) => dialog.accept());
  await other.getByRole("button", { name: "Delete", exact: true }).click();

  await expect(
    page.getByRole("button", { name: "Deleted elsewhere", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "New conversation", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL("/");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await other
    .getByRole("textbox", { name: "Message" })
    .fill("Created after remote deletion");
  await other.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByRole("button", {
      name: "Created after remote deletion",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Created after remote deletion", exact: true })
    .click();
  await expect(
    page.getByText("Saved conversation: Created after remote deletion", {
      exact: true,
    }),
  ).toBeVisible();
  await device.close();
});

test("retry a failed response without duplicating the message", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "Message" })
    .fill("Retry this response");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText("Response failed. Your messages are saved."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry response" }).click();
  await expect(
    page.getByText("Saved conversation: Retry this response", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".message").getByText("Retry this response", { exact: true }),
  ).toHaveCount(1);
});

test("frozen historical messages render with reasoning, tool retry results and original timestamps", async ({
  page,
}) => {
  const created = await page.request.post("/test/compatibility-thread");
  const thread = await created.json();
  await page.goto(`/?thread=${thread.id}`);
  await expect(
    page
      .locator(".message")
      .getByText("雪 I felt tired yesterday. Compare bicycle gearing.", {
        exact: true,
      }),
  ).toBeVisible();
  await expect(
    page.getByText("Fictional historical reasoning", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Fictional gearing comparison", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".tool-result summary")).toHaveCount(2);
  await page.locator(".tool-result summary").first().click();
  await expect(page.locator(".tool-result pre").first()).toContainText(
    "Use a valid ID",
  );
  await expect(page.locator(".message time").first()).toHaveAttribute(
    "datetime",
    "2026-10-01T12:00:00.000Z",
  );
  await page
    .getByRole("textbox", { name: "Message" })
    .fill("Continue fixture gearing");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    page.getByText(
      "Saved conversation: 雪 I felt tired yesterday. Compare bicycle gearing. | Continue fixture gearing",
      { exact: true },
    ),
  ).toBeVisible();
});
