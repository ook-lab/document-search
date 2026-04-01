import asyncio
from playwright.async_api import async_playwright
import os

async def generate_pdf():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        file_path = "file:///" + os.path.abspath("user_sample.html").replace("\\", "/")
        await page.goto(file_path)
        
        await page.wait_for_timeout(3000) # katexのレンダリング待ち
        
        # PDFに出力
        await page.pdf(
            path="user_output.pdf",
            format="A4",
            print_background=True
        )
        await browser.close()
        print("PDF user_output.pdf generated successfully.")

if __name__ == "__main__":
    asyncio.run(generate_pdf())
