import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

const pdf = Buffer.from("%PDF-1.7\nfictional block three for browser tests\n");

/** Upload the source PDF through a chat thread, then seed the plans that reference it. */
async function seedPlans(page: Page) {
  const upload = await page.request.post("/api/attachments", {
    multipart: {
      file: { name: "block-3.pdf", mimeType: "application/pdf", buffer: pdf },
    },
  });
  expect(upload.status()).toBe(201);
  const attachment = await upload.json();
  const thread = await (await page.request.post("/api/threads")).json();
  await page.request.post(`/api/threads/${thread.id}/messages`, {
    data: { text: "Here is my plan", attachment_ids: [attachment.id] },
  });
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/api/threads/${thread.id}`)).json()).run
          .status,
    )
    .toBe("completed");
  const seeded = await page.request.post("/test/plans", {
    data: {
      content_sha256: attachment.content_sha256,
      file_name: "block-3.pdf",
    },
  });
  expect(seeded.status()).toBe(200);
  return { ...(await seeded.json()), thread: thread.id };
}

test("the plan page shows weeks, key sessions and programmes, follows chat edits and offers sources", async ({
  page,
  browser,
}) => {
  const { block, thread } = await seedPlans(page);
  let subscriptions = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/events") subscriptions++;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Plans", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "Training plans",
    exact: true,
  });
  await dialog.getByRole("button", { name: /Fictional Block 3/ }).click();
  await expect(
    dialog.getByRole("heading", { name: "Fictional Block 3" }),
  ).toBeVisible();
  expect(page.url()).toContain(`?plan=${block}`);
  const subscribed = subscriptions;
  const weekOne = dialog.getByRole("region", { name: "Week of 2030-03-04" });
  await expect(weekOne).toContainText("base · 10–12 h");
  await expect(
    weekOne.getByRole("article", { name: "Key session Run intervals" }),
  ).toBeVisible();
  await expect(
    weekOne
      .getByLabel("2030-03-05")
      .getByRole("article", { name: "Session Swim program 1" }),
  ).toBeVisible();
  await expect(
    dialog.getByText("Active programmes: Fictional Gym Programme"),
  ).toBeVisible();
  await expect(
    dialog.getByRole("region", { name: "Open questions" }),
  ).toContainText("Which Sunday variant applies?");

  // Another device edits the plan in chat; the open page follows without a reload.
  const other = await browser.newPage();
  await other.goto(`/?thread=${thread}`);
  await other
    .getByRole("textbox", { name: "Message" })
    .fill("Move Tuesday's swim to Thursday");
  await other.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    other.getByText("Moved the swim to Thursday.", { exact: true }),
  ).toBeVisible();
  await expect(
    weekOne
      .getByLabel("2030-03-07")
      .getByRole("article", { name: "Session Swim program 1" }),
  ).toBeVisible();
  await expect(
    weekOne
      .getByLabel("2030-03-05")
      .getByRole("article", { name: "Session Swim program 1" }),
  ).toHaveCount(0);
  await expect(dialog.getByText(/version 2/)).toBeVisible();
  // Re-rendering the app must not reopen the plan page's event subscription.
  expect(subscriptions).toBe(subscribed);

  // The source stays downloadable after its thread is deleted.
  await other.request.delete(`/api/threads/${thread}`);
  expect((await other.request.get(`/api/plans/${block}`)).status()).toBe(200);
  const download = page.waitForEvent("download");
  await dialog.getByRole("link", { name: "block-3.pdf" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("block-3.pdf");
  expect(await readFile((await file.path())!)).toEqual(pdf);

  // Deleted from another device: the page says so and leads back to the list.
  await other.goto(`/?plan=${block}`);
  const otherDialog = other.getByRole("dialog", {
    name: "Training plans",
    exact: true,
  });
  await expect(
    otherDialog.getByRole("heading", { name: "Fictional Block 3" }),
  ).toBeVisible();
  other.on("dialog", (d) => void d.accept());
  await otherDialog.getByRole("button", { name: "Delete plan" }).click();
  await expect(otherDialog.getByText("Plan deleted.")).toBeVisible();
  await expect(dialog.getByText("This plan was deleted.")).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: /Fictional Block 3/ }),
  ).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: /Fictional Gym Programme/ }),
  ).toBeVisible();
  await other.close();
});
