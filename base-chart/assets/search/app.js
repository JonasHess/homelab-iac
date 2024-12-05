// app.js
const express = require('express');
const app = express();
const port = 3000;

app.use(express.static('public')); // Serve static files like CSS

app.get('/', (req, res) => {
    const query = req.query.q || '';
    res.send(`
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Search</title>
      <style>
        body {
          margin: 0; padding: 0; display: flex; flex-direction: column;
          justify-content: center; align-items: center; height: 100vh;
          background-color: #121212; color: #fff; font-family: Arial, sans-serif;
        }
        .search-container {
          text-align: center;
        }
        .search-bar {
          width: 50%; padding: 10px; font-size: 18px; border-radius: 5px;
          border: 1px solid #333; background-color: #333; color: #fff;
        }
        .buttons {
          margin-top: 10px;
        }
        .button {
          margin: 5px; padding: 10px 20px; font-size: 16px; border: none;
          border-radius: 5px; background-color: #1a73e8; color: #fff; cursor: pointer;
          text-decoration: none;
        }
        .button:hover {
          background-color: #1665c1;
        }
      </style>
    </head>
    <body>
      <div class="search-container">
        <input 
          class="search-bar" 
          type="text" 
          id="search" 
          placeholder="Search" 
          value="${query}" 
          oninput="updateQueryParam(this.value)"
        />
        <div class="buttons">
          <a class="button" id="googleBtn" href="https://www.google.com/search?q=${query}" target="_blank">Google Search</a>
          <a class="button" id="perplexityBtn" href="https://www.perplexity.ai/search?q=${query}" target="_blank">Perplexity Search</a>
        </div>
      </div>
      <script>
        function updateQueryParam(value) {
          const params = new URLSearchParams(window.location.search);
          params.set('q', value);
          window.history.replaceState({}, '', \`?\${params.toString()}\`);
          document.getElementById('googleBtn').href = \`https://www.google.com/search?q=\${value}\`;
          document.getElementById('perplexityBtn').href = \`https://www.perplexity.ai/search?q=\${value}\`;
        }
      </script>
    </body>
    </html>
  `);
});

app.listen(port, () => {
    console.log(`App running at http://localhost:${port}`);
});
