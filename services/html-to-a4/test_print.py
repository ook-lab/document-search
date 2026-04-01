import asyncio
from playwright.async_api import async_playwright

async def generate_pdf():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        # Flaskサーバーにアクセス (念のため少し待ったり、ダミーデータを入れる)
        await page.goto("http://127.0.0.1:5051/")
        
        # はみ出しテスト用の巨大なテキストや横長テーブルを挿入
        html_content = """
        <h1>A4テスト</h1>
        <p>普通のテキスト段落です。</p>
        <div style="background: #f0f0f0; padding: 10px; margin-bottom: 20px;">
            <p>あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをんあいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん</p>
        </div>
        
        <table border="1" style="width: 100%; border-collapse: collapse;">
            <tr>
                <td style="padding: 5px;">非常に長いデータの列AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</td>
                <td style="padding: 5px;">これも長いBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB</td>
            </tr>
        </table>
        """
        
        # テキストエリアに流し込んで submit
        await page.fill("textarea[name='html_content']", html_content)
        await page.click("button[type='submit']")
        await page.wait_for_load_state("networkidle")
        
        # PDFに出力
        await page.pdf(
            path="test_a4_output.pdf",
            format="A4",
            print_background=True
        )
        
        await browser.close()
        print("PDF test_a4_output.pdf generated successfully.")

if __name__ == "__main__":
    asyncio.run(generate_pdf())
