import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

const pdf = Buffer.from("%PDF-1.7\nfictional week plan for browser tests\n");

async function attach(page: Page, name = "week-plan.pdf") {
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment" }).click();
  await (
    await chooser
  ).setFiles({ name, mimeType: "application/pdf", buffer: pdf });
  return page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/attachments") &&
      response.request().method() === "POST",
  );
}

test("Attach a PDF, send it, reopen the thread and download it", async ({
  page,
}) => {
  await page.goto("/");
  const uploaded = await attach(page);
  expect(uploaded.status()).toBe(201);
  await expect(
    page.getByRole("group", { name: "Document attachment week-plan.pdf" }),
  ).toBeVisible();

  await page
    .getByRole("textbox", { name: "Message" })
    .fill("What does this plan say?");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  // The synthetic model reports the bytes it received in the attachment's turn.
  await expect(
    page.getByText(`received application/pdf ${pdf.length} bytes`),
  ).toBeVisible();

  await page.reload();
  // Sent attachments show their name and size and download when opened.
  const chip = page
    .locator(".message")
    .first()
    .getByRole("button", { name: `week-plan.pdf ${pdf.length} B` });
  await expect(chip).toBeVisible();
  await expect(
    page.locator(".message").first().getByText("What does this plan say?", {
      exact: true,
    }),
  ).toBeVisible();

  const download = page.waitForEvent("download");
  await chip.click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("week-plan.pdf");
  expect(await readFile((await file.path())!)).toEqual(pdf);
});

test("Removing a chip before sending deletes the upload", async ({ page }) => {
  await page.goto("/");
  const uploaded = await (await attach(page, "draft.pdf")).json();
  const removed = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/attachments/${uploaded.id}`) &&
      response.request().method() === "DELETE",
  );
  await page.getByRole("button", { name: "Remove draft.pdf" }).click();
  expect((await removed).status()).toBe(204);
  await expect(
    page.getByRole("group", { name: "Document attachment draft.pdf" }),
  ).toHaveCount(0);
  const content = await page.request.get(
    `/api/attachments/${uploaded.id}/content`,
  );
  expect(content.status()).toBe(404);
});

test("Attaching works on plain-HTTP origins without crypto.randomUUID", async ({
  page,
}) => {
  // Browsers expose randomUUID only in secure contexts, e.g. not on http://<LAN address>.
  await page.addInitScript(() => {
    Object.defineProperty(Crypto.prototype, "randomUUID", { value: undefined });
  });
  await page.goto("/");
  expect((await attach(page)).status()).toBe(201);
  await expect(
    page.getByRole("group", { name: "Document attachment week-plan.pdf" }),
  ).toBeVisible();
});

test("A rejected file is reported instead of ignored", async ({ page }) => {
  await page.goto("/");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment" }).click();
  await (
    await chooser
  ).setFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("fictional notes"),
  });
  await expect(
    page.getByText(/File type text\/plain is not accepted/),
  ).toBeVisible();
});
