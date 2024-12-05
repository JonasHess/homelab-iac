const express = require('express');
const app = express();
const port = 3000;

app.use(express.static('public')); // Serve static files if needed

// Serve the OpenSearch XML file
app.get('/opensearch.xml', (req, res) => {
    res.set({
        'Content-Type': 'application/opensearchdescription+xml',
        'Content-Disposition': 'inline; filename="opensearch.xml"'
    });
res.send(`<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
<ShortName>Multi Search</ShortName>
<Description>Search across multiple providers.</Description>
<Url type="text/html" method="get" template="https://search.hess.pm/?q={searchTerms}"/>
<InputEncoding>UTF-8</InputEncoding>
<OutputEncoding>UTF-8</OutputEncoding>
</OpenSearchDescription>`);
});

app.get('/', (req, res) => {
    const query = req.query.q || '';
    res.send(`
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Search</title>
      <!-- Bootstrap 5.0.2 CDN -->
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-EVSTQN3/azprG1Anm3QDgpJLIm9Nao0Yz1ztcQTwFspd3yD65VohhpuuCOmLASjC" crossorigin="anonymous">
      <link rel="search" type="application/opensearchdescription+xml" title="MultiProvider Search" href="/opensearch.xml" />
      <style>
        /* Styling for the selected button */
        .selected {
          background-color: #1a73e8 !important; /* New background color */
          color: white !important; /* Ensure text is white */
        }
      </style>
    </head>
    <body class="bg-dark text-white d-flex flex-column justify-content-center align-items-center vh-100">
      <div class="container text-center">
        <div class="row">
          <div class="col-12">
            <input
              class="form-control form-control-lg"
              type="text"
              id="search"
              placeholder="Search"
              value="${query}"
            />
          </div>
        </div>
        <div class="row mt-4" id="buttons">
          <!-- Buttons will be dynamically added here -->
        </div>
      </div>

      <!-- Bootstrap JS and dependencies -->
      <script src="https://cdn.jsdelivr.net/npm/@popperjs/core@2.11.6/dist/umd/popper.min.js" integrity="sha384-oBqDVmMz4fnFO9gyb6+OK6Cw/W2x2AW4D6p6b/jR6Hkxgq6fflNw3zbhJgQ0y3Uj" crossorigin="anonymous"></script>
      <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/js/bootstrap.min.js" integrity="sha384-pzjw8f+ua7Kw1TIq0Gv1f7CnrD0g5j1H9ZZ4JlvEZj4RjPj8INsA1NTO7kmrV5qZ" crossorigin="anonymous"></script>
      <script>
        // Define search buttons and their properties
        const searchProviders = [
          { name: 'Google', key: 'g', url: 'https://www.google.com/search?q=', id: 'googleBtn', isDefault: true },
          { name: 'Perplexity', key: 'p', url: 'https://www.perplexity.ai/search?q=', id: 'perplexityBtn' },
          { name: 'dict.cc', key: 'd', url: 'https://www.dict.cc/?s=', id: 'dictBtn' },
          { name: 'Amazon.de', key: 'a', url: 'https://www.amazon.de/s?k=', id: 'amazonBtn' },
          { name: 'ChatGPT', key: 'c', url: 'https://chat.openai.com/chat?q=', id: 'chatgptBtn' },
          { name: 'YouTube', key: 'y', url: 'https://www.youtube.de/results?search_query=', id: 'youtubeBtn' },
          { name: 'Google Maps', key: 'm', url: 'https://www.google.com/maps/search/', id: 'googleMapsBtn' },
          { name: 'Makerworld', key: 'w', url: 'https://www.makerworld.com/search?keyword=', id: 'makerworldBtn' }
        ];

        // DOM Elements
        const buttonsContainer = document.getElementById('buttons');
        const searchBar = document.getElementById('search');

        let selectedProvider = searchProviders.find(p => p.isDefault); // Default selected provider
        let isSearchBarFocused = true; // Track focus state of search bar

        // Create buttons dynamically and wrap each in a div
        searchProviders.forEach(provider => {
          const buttonDiv = document.createElement('div');
          buttonDiv.className = 'col-12 col-md-4 mb-2'; // Bootstrap grid and margin for spacing

          const button = document.createElement('a');
          button.className = 'btn btn-outline-primary w-100'; // Button with width 100%
          button.id = provider.id;
          button.href = provider.url + (new URLSearchParams(window.location.search).get('q') || '');
          button.target = '_blank';
          button.textContent = provider.name;

          if (provider === selectedProvider) {
            button.classList.add('selected');
          }

          buttonDiv.appendChild(button);
          buttonsContainer.appendChild(buttonDiv);
        });

        // Update query and button states
        function updateQueryParam(value) {
          const params = new URLSearchParams(window.location.search);
          params.set('q', value);
          window.history.replaceState({}, '', \`?\${params.toString()}\`);
          searchProviders.forEach(provider => {
            document.getElementById(provider.id).href = provider.url + value;
          });
        }

        // Handle Enter and Escape Keys
        document.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            if (isSearchBarFocused) {
              searchBar.blur(); // Unfocus search bar
              isSearchBarFocused = false;
            } else {
              // Redirect to selected provider
              window.open(document.getElementById(selectedProvider.id).href, '_blank');
            }
          } else if (e.key === 'Escape') {
            searchBar.focus(); // Focus search bar
            isSearchBarFocused = true;
          } else if (!isSearchBarFocused) {
            handleShortcuts(e);
          }
        });

        // Manage focus on search bar
        searchBar.addEventListener('focus', () => {
          isSearchBarFocused = true;
          updateProviderHighlight();
        });

        searchBar.addEventListener('blur', () => {
          isSearchBarFocused = false;
          updateProviderHighlight();
        });

        // Update on input
        searchBar.addEventListener('input', (e) => {
          updateQueryParam(e.target.value);
        });

        // Focus the search bar on page load
        searchBar.focus();
        const value = searchBar.value;
        searchBar.setSelectionRange(value.length, value.length);

        // Update selected provider
        function selectProvider(provider) {
          selectedProvider = provider;
          updateProviderHighlight();
        }

        // Update provider highlight based on focus state
        function updateProviderHighlight() {
          document.querySelectorAll('.btn').forEach(button => button.classList.remove('selected'));
          if (!isSearchBarFocused) {
            document.getElementById(selectedProvider.id).classList.add('selected');
          }
        }

        // Cycle through providers with arrow keys
        function cycleProvider(direction) {
          const currentIndex = searchProviders.indexOf(selectedProvider);
          const newIndex = (currentIndex + direction + searchProviders.length) % searchProviders.length;
          selectProvider(searchProviders[newIndex]);
        }

        // Handle keyboard shortcuts for provider selection
        function handleShortcuts(e) {
          if (e.key === 'ArrowRight'){
            cycleProvider(1); // Cycle to the next provider
          }
            else if (e.key === 'ArrowDown'){
            cycleProvider(3); // Cycle to the next provider
          } else if (e.key === 'ArrowLeft'){
            cycleProvider(-1); // Cycle to the previous provider
          }
            else if (e.key === 'ArrowUp'){
            cycleProvider(-3); // Cycle to the previous provider
          } else {
            const provider = searchProviders.find(p => p.key === e.key.toLowerCase());
            if (provider) {
              selectProvider(provider);
            }
          }
        }

        // Initial highlight update
        updateProviderHighlight();
      </script>
    </body>
    </html>
    `);
});

app.listen(port, () => {
    console.log(`App running at http://localhost:${port}`);
});