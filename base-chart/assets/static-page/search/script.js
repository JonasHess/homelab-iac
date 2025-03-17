// Define search buttons and their properties
const searchProviders = [
    { name: 'Google', key: 'g', url: 'https://www.google.com/search?q=', id: 'googleBtn', isDefault: true },
    { name: 'Perplexity', key: 'p', url: 'https://www.perplexity.ai/search?q=', id: 'perplexityBtn' },
    { name: 'ChatGPT', key: 'c', url: 'https://chat.openai.com/?hints=search&q=', id: 'chatgptBtn' },
    { name: 'Claude.ai', key: 'l', url: 'https://claude.ai/chat?query=', id: 'claudeBtn' },
    { name: 'dict.cc', key: 'd', url: 'https://www.dict.cc/?s=', id: 'dictBtn' },
    { name: 'Amazon.de', key: 'a', url: 'https://www.amazon.de/s?k=', id: 'amazonBtn' },
    { name: 'YouTube', key: 'y', url: 'https://www.youtube.de/results?search_query=', id: 'youtubeBtn' },
    { name: 'Google Maps', key: 'm', url: 'https://www.google.com/maps/search/', id: 'googleMapsBtn' },
    { name: 'Makerworld', key: 'w', url: 'https://www.makerworld.com/search?keyword=', id: 'makerworldBtn' },
    { name: 'Geizhals.de', key: 'z', url: 'https://geizhals.de/?fs=', id: 'geizhalsBtn' }
];

// DOM Elements
const buttonsContainer = document.getElementById('buttons');
const searchBar = document.getElementById('search');
const searchHistory = document.getElementById('searchHistory');
const typingFeedback = document.getElementById('typingFeedback');
const voiceSearchBtn = document.getElementById('voiceSearchBtn');
const clearSearchBtn = document.getElementById('clearSearchBtn');
const searchLoader = document.getElementById('searchLoader');
const serviceFilter = document.getElementById('serviceFilter');

let selectedProvider = searchProviders.find(p => p.isDefault); // Default selected provider
let searchHistoryArray = JSON.parse(localStorage.getItem('searchHistory')) || [];
let isListening = false;
let observerInitialized = false;
let activeHistoryIndex = -1; // Track selected history item
let isSearchBarFocused = false; // Track if search bar is focused

// Get query parameter from URL
function getQueryParam() {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('q') || '';
}

// Set initial search value from URL
searchBar.value = getQueryParam();

// Create buttons dynamically with icons and shortcuts
searchProviders.forEach(provider => {
    const button = document.createElement('a');
    button.className = 'provider-btn';
    button.id = provider.id;
    button.href = provider.url + getQueryParam();
    button.target = '_blank';

    const btnContent = document.createElement('span');
    btnContent.textContent = provider.name;

    button.appendChild(btnContent);

    if (provider === selectedProvider) {
        button.classList.add('selected');
    }

    buttonsContainer.appendChild(button);

    // Add button click handler
    button.addEventListener('click', function(e) {
        // Only prevent default if we're just selecting
        if (!isSearchBarFocused) {
            e.preventDefault();
            selectProvider(provider);
        } else if (searchBar.value.trim() !== '') {
            // If we have a search term, save to history
            addToSearchHistory(searchBar.value.trim());
            showLoader();
        }
    });
});

// Update query and button states
function updateQueryParam(value) {
    const params = new URLSearchParams(window.location.search);
    params.set('q', value);
    window.history.replaceState({}, '', `?${params.toString()}`);
    searchProviders.forEach(provider => {
        document.getElementById(provider.id).href = provider.url + value;
    });

    // Update typing feedback animation
    if (value.length > 0) {
        typingFeedback.style.transform = 'scaleX(1)';
    } else {
        typingFeedback.style.transform = 'scaleX(0)';
    }
}

// Search history functions
function addToSearchHistory(query) {
    // Don't add duplicates or empty queries
    if (query && !searchHistoryArray.includes(query)) {
        // Add to the beginning of the array
        searchHistoryArray.unshift(query);

        // Keep only the last 10 searches
        if (searchHistoryArray.length > 10) {
            searchHistoryArray = searchHistoryArray.slice(0, 10);
        }

        // Save to localStorage
        localStorage.setItem('searchHistory', JSON.stringify(searchHistoryArray));
    }
}

function showSearchHistory() {
    // Clear previous history
    searchHistory.innerHTML = '';

    if (searchHistoryArray.length === 0) {
        searchHistory.style.display = 'none';
        return;
    }

    // Reset active index
    activeHistoryIndex = -1;

    // Populate history items
    searchHistoryArray.forEach((query, index) => {
        const historyItem = document.createElement('div');
        historyItem.className = 'history-item';
        historyItem.dataset.index = index;

        const queryText = document.createElement('span');
        queryText.textContent = query;
        historyItem.appendChild(queryText);

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'delete-history';
        deleteBtn.innerHTML = '<i class="fas fa-times"></i>';
        deleteBtn.title = 'Remove from history';
        deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Prevent clicking the parent
            removeHistoryItem(index);
        });
        historyItem.appendChild(deleteBtn);

        historyItem.addEventListener('click', () => {
            searchBar.value = query;
            updateQueryParam(query);
            searchHistory.style.display = 'none';
        });

        searchHistory.appendChild(historyItem);
    });

    // Add clear all button
    const historyActions = document.createElement('div');
    historyActions.className = 'history-actions';

    const clearAllBtn = document.createElement('button');
    clearAllBtn.className = 'clear-history-btn';
    clearAllBtn.innerHTML = '<i class="fas fa-trash-alt"></i> Clear history';
    clearAllBtn.addEventListener('click', clearSearchHistory);

    historyActions.appendChild(clearAllBtn);
    searchHistory.appendChild(historyActions);

    searchHistory.style.display = 'block';
}

// Remove a specific history item
function removeHistoryItem(index) {
    searchHistoryArray.splice(index, 1);
    localStorage.setItem('searchHistory', JSON.stringify(searchHistoryArray));
    showSearchHistory();
}

// Clear all search history
function clearSearchHistory() {
    searchHistoryArray = [];
    localStorage.setItem('searchHistory', JSON.stringify(searchHistoryArray));
    searchHistory.style.display = 'none';
}

// Navigate search history with arrow keys
function navigateSearchHistory(direction) {
    if (searchHistoryArray.length === 0 || searchHistory.style.display === 'none') {
        return;
    }

    // Clear current selection
    const items = searchHistory.querySelectorAll('.history-item');
    items.forEach(item => item.classList.remove('active'));

    // Update index
    activeHistoryIndex += direction;

    // Handle wrapping
    if (activeHistoryIndex < 0) {
        activeHistoryIndex = searchHistoryArray.length - 1;
    } else if (activeHistoryIndex >= searchHistoryArray.length) {
        activeHistoryIndex = 0;
    }

    // Highlight selected item
    const selectedItem = items[activeHistoryIndex];
    selectedItem.classList.add('active');
    selectedItem.scrollIntoView({ block: 'nearest' });

    // Update search box
    searchBar.value = searchHistoryArray[activeHistoryIndex];
    updateQueryParam(searchBar.value);
}

// Voice search functionality
function startVoiceRecognition() {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        const recognition = new SpeechRecognition();

        recognition.continuous = false;
        recognition.interimResults = true;

        recognition.onstart = function() {
            isListening = true;
            voiceSearchBtn.classList.add('voice-active');
            searchBar.placeholder = "Listening...";
        };

        recognition.onresult = function(event) {
            const transcript = event.results[0][0].transcript;
            searchBar.value = transcript;
            updateQueryParam(transcript);
        };

        recognition.onerror = function(event) {
            console.error('Speech recognition error', event.error);
            isListening = false;
            voiceSearchBtn.classList.remove('voice-active');
            searchBar.placeholder = "What are you looking for today?";
        };

        recognition.onend = function() {
            isListening = false;
            voiceSearchBtn.classList.remove('voice-active');
            searchBar.placeholder = "What are you looking for today?";
        };

        recognition.start();
    } else {
        alert("Voice recognition is not supported by your browser.");
    }
}

// Loading indicator
function showLoader() {
    searchLoader.style.display = 'inline-block';
    setTimeout(() => {
        searchLoader.style.display = 'none';
    }, 1000); // Hide after 1 second
}

// Handle Enter and Escape Keys
document.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
        // Toggle between the two input fields only
        if (document.activeElement === searchBar) {
            e.preventDefault();
            serviceFilter.focus();
        } else if (document.activeElement === serviceFilter) {
            e.preventDefault();
            searchBar.focus();
        }
    } else if (e.key === 'Enter') {
        // Handle Enter based on active element
        if (document.activeElement === searchBar) {
            e.preventDefault();
            // Always set focus to the smaller input field
            serviceFilter.focus();
        } else if (document.activeElement === serviceFilter) {
            e.preventDefault();
            // Execute search when Enter is pressed in the filter field
            if (searchBar.value.trim() !== '') {
                addToSearchHistory(searchBar.value.trim());
                showLoader();
                window.location.href = document.getElementById(selectedProvider.id).href;
            }
        }
    } else if (e.key === 'Escape') {
        // Clear the current field
        if (document.activeElement === searchBar) {
            searchBar.value = '';
            updateQueryParam('');
            searchHistory.style.display = 'none';
        } else if (document.activeElement === serviceFilter) {
            serviceFilter.value = '';
            filterServices('');
        }
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        // Handle arrow keys differently based on active element
        if (document.activeElement === searchBar) {
            // Navigate search history
            if (searchHistory.style.display === 'block') {
                e.preventDefault();
                navigateSearchHistory(e.key === 'ArrowUp' ? -1 : 1);
            }
        } else if (document.activeElement === serviceFilter) {
            // Select previous/next service
            const allBtns = Array.from(document.querySelectorAll('.provider-btn'));
            const currentIndex = allBtns.findIndex(btn => btn.id === selectedProvider.id);
            let newIndex;

            if (e.key === 'ArrowUp') {
                newIndex = (currentIndex - 1 + allBtns.length) % allBtns.length;
            } else {
                newIndex = (currentIndex + 1) % allBtns.length;
            }

            // Get the provider from the button id
            const newProviderId = allBtns[newIndex].id;
            const newProvider = searchProviders.find(p => p.id === newProviderId);
            selectProvider(newProvider);
        }
    } else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && document.activeElement === serviceFilter) {
        // Handle left/right arrow keys in the filter field
        const allBtns = Array.from(document.querySelectorAll('.provider-btn'));
        const currentIndex = allBtns.findIndex(btn => btn.id === selectedProvider.id);
        let newIndex;

        if (e.key === 'ArrowLeft') {
            newIndex = (currentIndex - 1 + allBtns.length) % allBtns.length;
        } else {
            newIndex = (currentIndex + 1) % allBtns.length;
        }

        // Get the provider from the button id
        const newProviderId = allBtns[newIndex].id;
        const newProvider = searchProviders.find(p => p.id === newProviderId);
        selectProvider(newProvider);
    }
});

// Track focus state of search bar
searchBar.addEventListener('focus', () => {
    isSearchBarFocused = true;
    if (searchBar.value.trim() === '') {
        showSearchHistory();
    }
});

searchBar.addEventListener('blur', () => {
    isSearchBarFocused = false;
    // Hide search history with a slight delay
    setTimeout(() => {
        searchHistory.style.display = 'none';
    }, 200);
});

// Service filter input handler
serviceFilter.addEventListener('input', (e) => {
    filterServices(e.target.value);
});

// Update on input
searchBar.addEventListener('input', (e) => {
    updateQueryParam(e.target.value);
    if (e.target.value.trim() === '') {
        showSearchHistory();
    } else {
        searchHistory.style.display = 'none';
    }
});

// Focus the search bar on page load
searchBar.focus();
const value = searchBar.value;
searchBar.setSelectionRange(value.length, value.length);

// Clear search button handler
clearSearchBtn.addEventListener('click', () => {
    searchBar.value = '';
    updateQueryParam('');
    searchBar.focus();
    typingFeedback.style.transform = 'scaleX(0)';
    showSearchHistory();
});

// Voice search button handler
voiceSearchBtn.addEventListener('click', () => {
    if (!isListening) {
        startVoiceRecognition();
    }
});

// Update selected provider
function selectProvider(provider) {
    selectedProvider = provider;
    updateProviderHighlight();

    // Visual feedback for keyboard selection
    const button = document.getElementById(provider.id);
    button.classList.add('shortcut-press');
    setTimeout(() => {
        button.classList.remove('shortcut-press');
    }, 300);
}

// Update provider highlight
function updateProviderHighlight() {
    document.querySelectorAll('.provider-btn').forEach(button => button.classList.remove('selected'));
    document.getElementById(selectedProvider.id).classList.add('selected');
}

// Filter and order services based on input
function filterServices(filterText) {
    let orderedProviders;

    if (!filterText.trim()) {
        // If filter is empty, restore original order
        orderedProviders = [...searchProviders];
    } else {
        // Calculate relevance score for each provider
        const scoredProviders = searchProviders.map(provider => {
            // Different scoring factors
            let score = 0;
            const name = provider.name.toLowerCase();
            const filter = filterText.toLowerCase();

            // Exact match is best
            if (name === filter) {
                score += 100;
            }

            // Starting with filter is very good
            if (name.startsWith(filter)) {
                score += 50;
            }

            // Contains filter as a word part is good
            if (name.includes(filter)) {
                score += 25;
            }

            // Words in the name starting with filter are also good
            const words = name.split(/\s+/);
            for (const word of words) {
                if (word.startsWith(filter)) {
                    score += 15;
                }
            }

            // Character-by-character match for partial matching
            let matchCount = 0;
            for (let i = 0; i < filter.length && i < name.length; i++) {
                if (filter[i] === name[i]) {
                    matchCount++;
                }
            }
            score += (matchCount / filter.length) * 10;

            return { provider, score };
        });

        // Sort by score (higher is better)
        scoredProviders.sort((a, b) => b.score - a.score);

        // Extract just the providers in the new order
        orderedProviders = scoredProviders.map(item => item.provider);
    }

    // Update the display
    renderProvidersInOrder(orderedProviders);

    // Always select the first provider in the filtered list
    if (orderedProviders.length > 0) {
        selectProvider(orderedProviders[0]);
    }
}

// Render the providers in the given order
function renderProvidersInOrder(providers) {
    // Clear the container
    buttonsContainer.innerHTML = '';

    // Add buttons in the new order
    providers.forEach(provider => {
        const button = document.createElement('a');
        button.className = 'provider-btn';
        button.id = provider.id;
        button.href = provider.url + searchBar.value;
        button.target = '_blank';

        const btnContent = document.createElement('span');
        btnContent.textContent = provider.name;

        button.appendChild(btnContent);

        if (provider === selectedProvider) {
            button.classList.add('selected');
        }

        buttonsContainer.appendChild(button);

        // Add button click handler
        button.addEventListener('click', function(e) {
            e.preventDefault();
            selectProvider(provider);

            if (searchBar.value.trim() !== '') {
                // If we have a search term, save to history and redirect
                addToSearchHistory(searchBar.value.trim());
                showLoader();
                window.location.href = provider.url + searchBar.value;
            }
        });
    });
}

// Intersection Observer for lazy loading buttons
function initIntersectionObserver() {
    if (!observerInitialized && 'IntersectionObserver' in window) {
        const options = {
            root: null,
            rootMargin: '0px',
            threshold: 0.1
        };

        const observer = new IntersectionObserver((entries, observer) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.style.opacity = '1';
                    entry.target.style.transform = 'translateY(0)';
                    observer.unobserve(entry.target);
                }
            });
        }, options);

        // Add animation setup to buttons
        document.querySelectorAll('.provider-btn').forEach((btn, index) => {
            btn.style.opacity = '0';
            btn.style.transform = 'translateY(20px)';
            btn.style.transition = `all 0.3s ease ${index * 0.05}s`;
            observer.observe(btn);
        });

        observerInitialized = true;
    } else {
        // Fallback for browsers without IntersectionObserver
        document.querySelectorAll('.provider-btn').forEach((btn, index) => {
            btn.style.opacity = '1';
            btn.style.transform = 'translateY(0)';
            btn.style.transition = `all 0.3s ease ${index * 0.05}s`;
        });
    }
}

// Initial setup
updateProviderHighlight();
initIntersectionObserver();