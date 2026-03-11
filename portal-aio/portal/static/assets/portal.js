// Create a namespace for your application
window.InstancePortal = (function() {
    // Escape strings for safe HTML interpolation
    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
    // Escape strings for safe use inside JS string literals in onclick attributes
    function escapeAttr(str) {
        return str.replace(/&/g, '&amp;').replace(/'/g, '&#39;').replace(/"/g, '&quot;');
    }

    const applications = {
        // Store for application data
        _data: {},
        
        // Initialize applications
        init: async function() {
            try {
                await this.fetchApplications();
                this.renderAppGrid();
                await this.fetchDirectUrls();
                this.updateAllUI();
                
                // Optionally set up a refresh interval
                this._refreshInterval = setInterval(() => this.refreshData(), 30000);
            } catch (error) {
                console.error('Error initializing applications:', error);
            }
            return this;
        },
        
        // Fetch applications from API
        fetchApplications: async function() {
            try {
                const response = await fetch('/get-applications');
                
                if (!response.ok) {
                    throw new Error(`HTTP error! Status: ${response.status}`);
                }
                
                const data = await response.json();
                
                // Store the raw data
                this._data = data;
                
                // Add computed properties and methods to each application
                this._enhanceApplications();
                
                return data;
            } catch (error) {
                console.error('Error fetching applications:', error);
                return {};
            }
        },
        
        // Render the application grid
        renderAppGrid: function() {
            const appGrid = document.getElementById('app-grid');
            if (!appGrid) {
                console.error('Could not find app-grid element');
                return;
            }
            
            // Clear existing content
            appGrid.innerHTML = '';
            
            // Create a card for each application
            for (const [appName, app] of Object.entries(this._data)) {
                // Adjust display and behavior for Instance Portal
                const isInstancePortal = appName === "Instance Portal";
                const isDisabled = isInstancePortal ? 'disabled' : '';
                const buttonText = isInstancePortal ? 'Currently Active' : 'Launch Application';
        
                const cardHtml = `
                    <div class="card" data-app-id="${appName}">
                        <div class="card-content">
                            <div class="card-header">
                                <h2 class="app-name">${appName}</h2>
                            </div>
                        
                            <div class="launch-application" data-app-id="${appName}">
                                <button class="launch-btn" data-action="launch" ${isDisabled}>
                                    ${buttonText}
                                </button>
                            </div>
                            
                            <div class="advanced-section">
                                <button class="advanced-toggle" data-action="toggle-advanced">
                                    Advanced Connection Options
                                </button>
                                
                                <div class="advanced-content">
                                    <div class="advanced-details" data-app-id="${appName}"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
                
                // Append to the grid
                appGrid.innerHTML += cardHtml;
            }
            
            // Set up event listeners for the newly created elements
            this.setupEventListeners();
        },
        
        // Add computed properties and methods to application objects
        _enhanceApplications: function() {
            for (const [appName, app] of Object.entries(this._data)) {
                // Initialize properties
                app._direct_url = null;
                // Add getters for computed properties
                Object.defineProperties(app, {
                    "named_tunnel": {
                        get() {
                            try {
                                return tunnels.named_tunnels[this.target_url];
                            } catch {
                                return null;
                            }
                        }
                    },
                    "named_tunnel_url": {
                        get() {
                            return this.named_tunnel ? this.named_tunnel.tunnelUrl + this.open_path : null;
                        }
                    },
                    "quick_tunnel": {
                        get() {
                            try {
                                return tunnels.quick_tunnels[this.target_url];
                            } catch {
                                return null;
                            }
                        }
                    },
                    "quick_tunnel_url": {
                        get() {
                            return this.quick_tunnel ? this.quick_tunnel.tunnelUrl + this.open_path : null;
                        }
                    },
                    "direct_url": {
                        get() {
                            return this._direct_url ? this._direct_url + this.open_path : null;
                        },
                        set(value) {
                            this._direct_url = value;
                        }
                    },
                    "direct_url_full": {
                        get() {
                            let redir_flag = "";
                            if (this.external_port == 1111) {
                                redir_flag = "&redir=false";
                            }
                            return this._direct_url ? this._direct_url + this.open_path + redir_flag : null;
                        }
                    }
                });
                
                // Add methods
                app.isProxied = function() {
                    return this.external_port !== this.internal_port;
                };
                
                // UI rendering methods
                app.renderUI = function() {
                    // Find all elements for this app
                    const elements = document.querySelectorAll(`[data-app-id="${CSS.escape(appName)}"]`);
                    
                    elements.forEach(el => {
                        // For advanced details, update the content
                        if (el.classList.contains('advanced-details')) {
                            el.innerHTML = this.renderAdvancedDetails();
                        }
                        
                        // Launch button handlers are set in setupEventListeners
                    });
                };
                
                // Method to generate advanced details HTML
                app.renderAdvancedDetails = function() {
                    let html = '';
                    
                    if (this.direct_url) {
                        html += `
                        <div class="item">
                            <div>
                                <div>Port: ${this.external_port}${this.mapped_port ? " → " + this.mapped_port : ""}</div>
                                <div class="ip-info">IP: <a href="${this.direct_url_full}" target="_blank">${this.direct_url.split('//')[1].split(':')[0]}</a></div>
                            </div>
                            <button class="copy-btn" onclick="window.app.url.copy('${escapeAttr(this.direct_url_full)}')">
                                Copy URL
                            </button>
                        </div>
                        `;
                    }
                    
                    if (this.named_tunnel) {
                        html += `
                            <div class="item">
                                <div>
                                    <div>Port: ${this.external_port} → Named Tunnel</div>
                                    <div class="ip-info">Link: <a href="${this.named_tunnel_url}" target="_blank">Secure Tunnel Link</a></div>
                                </div>
                                <button class="copy-btn" onclick="window.app.url.copy('${escapeAttr(this.named_tunnel_url)}')">
                                    Copy URL
                                </button>
                            </div>
                        `;
                    }

                    if (this.quick_tunnel) {
                        html += `
                            <div class="item">
                                <div>
                                    <div>Port: ${this.external_port} → Quick Tunnel</div>
                                    <div class="ip-info">Link: <a href="${this.quick_tunnel_url}" target="_blank">Secure Tunnel Link</a></div>
                                </div>
                                <button class="copy-btn" onclick="window.app.url.copy('${escapeAttr(this.quick_tunnel_url)}')">
                                    Copy URL
                                </button>
                            </div>
                        `;
                    }
                    
                    return html;
                };
            }
        },
        
        // Set up event listeners
        setupEventListeners: function() {
            // Set up advanced toggle handlers
            document.querySelectorAll('[data-action="toggle-advanced"]').forEach(btn => {
                btn.onclick = () => {
                    const card = btn.closest('.card');
                    if (!card) return;
                    
                    const appId = card.getAttribute('data-app-id');
                    this.toggleAdvanced(appId);
                };
            });
            
            // Set up launch button handlers
            document.querySelectorAll('[data-action="launch"]').forEach(btn => {
                btn.onclick = () => {
                    const appElement = btn.closest('[data-app-id]');
                    if (!appElement) return;
                    
                    const appId = appElement.getAttribute('data-app-id');
                    const app = this._data[appId];
                    
                    if (!app) return;
                    
                    let url = null;
                    if (app.named_tunnel && app.named_tunnel.status == "active") {
                        url = app.named_tunnel_url;
                    } else if (app.direct_url_full && instance.direct_https === "true") {
                        url = app.direct_url_full;
                    } else if (app.quick_tunnel && app.quick_tunnel.status == "active") {
                        url = app.quick_tunnel_url;
                    } else if (app.direct_url_full) {
                        url = app.direct_url_full;
                    }
                    
                    if (url) {
                        window.open(url, '_blank');
                    } else {
                        window.app.showToast("No URL is available", "error");
                    }
                };
            });
        },
        
        // Toggle the advanced details section for an application
        toggleAdvanced: function(appId) {
            const card = document.querySelector(`.card[data-app-id="${appId}"]`);
            if (!card) return;
            
            const advancedContent = card.querySelector('.advanced-content');
            if (advancedContent) {
                advancedContent.classList.toggle('show');
            }
        },
        
        // Utility methods
        findByTargetUrl: function(targetUrl) {
            const matches = [];
            
            for (const [appName, app] of Object.entries(this._data)) {
                if (app.target_url === targetUrl) {
                    matches.push(app);
                }
            }
            
            return matches;
        },
        
        findByPort: function(port) {
            const portNum = typeof port === 'string' ? parseInt(port, 10) : port;
            const matches = [];
            
            for (const [appName, app] of Object.entries(this._data)) {
                if (app.external_port === portNum || app.internal_port === portNum) {
                    matches.push(app);
                }
            }
            
            return matches;
        },
        
        // Fetch direct URLs for all applications
        fetchDirectUrls: async function() {
            const MAX_RETRIES = 5;
            const RETRY_DELAY = 2000; // 2 seconds
            
            for (const [appName, app] of Object.entries(this._data)) {
                let success = false;
                
                for (let attempt = 1; attempt <= MAX_RETRIES && !success; attempt++) {
                    try {
                        const response = await fetch(`/get-direct-url/${app.external_port}`);
                        
                        if (response.ok) {
                            const data = await response.json();
                            
                            // Store the direct URL using the setter
                            app.direct_url = data.result;
                            success = true;
                        } else {
                            if (attempt < MAX_RETRIES) {
                                await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
                            } else {
                                app.direct_url = null;
                                app.direct_url_error = 'Failed to retrieve direct URL';
                            }
                        }
                    } catch (error) {
                        if (attempt < MAX_RETRIES) {
                            await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
                        } else {
                            console.error(`Error fetching direct URL for ${appName}:`, error);
                            app.direct_url = null;
                            app.direct_url_error = 'Connection error';
                        }
                    }
                }
            }
        },
        
        // Update UI of all applications
        updateAllUI: function() {
            // First remove any loading indicator if it exists
            const loadingIndicator = document.getElementById('loading-indicator');
            if (loadingIndicator) {
                loadingIndicator.remove();
            }
            
            // Update UI for all applications
            for (const [appName, app] of Object.entries(this._data)) {
                if (app.renderUI) {
                    app.renderUI();
                }
            }
        },
        
        // Refresh data
        refreshData: async function() {
            await this.fetchApplications();
            await this.fetchDirectUrls();
            this.updateAllUI();
        }
    };
    
    const tunnels = {
        // Data storage
        named_tunnels: {},
        quick_tunnels: {},
        
        // Tunnel factory for creating standardized tunnel objects (internal method)
        _createTunnelObject: function(type, targetUrl, tunnelUrl) {
            const parent = this;

            const tunnel = {
                type: type,           // 'named' or 'quick'
                targetUrl: targetUrl, // The local URL being tunneled
                tunnelUrl: tunnelUrl, // The public tunnel URL
                createdAt: new Date(),
                status: 'pending',    // 'pending', 'active', 'error'
                
                // Methods that can be called on a tunnel instance
                isActive: async function() {
                    const isResolving = await parent.canResolve(this.tunnelUrl);
                    this.status = isResolving ? 'active' : 'error';
                    return isResolving;
                },
                
                getInfo: function() {
                    return {
                        type: this.type,
                        targetUrl: this.targetUrl,
                        tunnelUrl: this.tunnelUrl,
                        createdAt: this.createdAt,
                        status: this.status
                    };
                },
                
                toString: function() {
                    return `${this.type} tunnel: ${this.targetUrl} → ${this.tunnelUrl} (${this.status})`;
                },
                
                // Method to stop a quick tunnel
                stop: async function() {
                    if (this.type !== 'quick') {
                        console.error('Only quick tunnels can be stopped');
                        return false;
                    }
                    
                    const result = await parent.stopQuickTunnel(this.targetUrl);
                    return result ? true : false;
                },
                
                // Method to refresh a quick tunnel
                refresh: async function() {
                    if (this.type !== 'quick') {
                        console.error('Only quick tunnels can be refreshed');
                        return false;
                    }
                    
                    const result = await parent.refreshQuickTunnel(this.targetUrl);
                    if (result && result.tunnel_url) {
                        this.tunnelUrl = result.tunnel_url;
                        this.status = 'pending';
                        await this.isActive(); // Check if the new tunnel is active
                        return true;
                    }
                    return false;
                }
            };
            
            return tunnel;
        },

        // API Methods
        fetchNamedTunnels: async function() {
            const MAX_RETRIES = 5;
            const RETRY_DELAY = 2000; // 2 seconds
            
            for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
                try {
                    const response = await fetch('/get-named-tunnels');
                    
                    // Don't retry if it's a 404 - that means the resource doesn't exist
                    if (response.status === 404) {
                        return [];
                    }
                    
                    if (!response.ok) {
                        throw new Error(`Failed to fetch named tunnels: ${response.statusText}`);
                    }
                    
                    const tunnelsData = await response.json();
                    
                    // Process each tunnel and associate with applications
                    tunnelsData.forEach(tunnelData => {
                        const tunnel = this._createTunnelObject('named', tunnelData.targetUrl, tunnelData.tunnelUrl);
                        this.named_tunnels[tunnelData.targetUrl] = tunnel;
                    });
                    
                    return Object.values(this.named_tunnels);
                } catch (error) {
                    if (attempt < MAX_RETRIES) {
                        await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
                    } else {
                        return [];
                    }
                }
            }
        },
        
        fetchQuickTunnels: async function() {
            const MAX_RETRIES = 5;
            const RETRY_DELAY = 2000; // 2 seconds
            
            for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
                try {
                    const response = await fetch('/get-all-quick-tunnels');
                    
                    if (!response.ok) {
                        throw new Error(`Failed to fetch quick tunnels: ${response.statusText}`);
                    }
                    
                    const tunnelsData = await response.json();
                    
                    // Process each tunnel and associate with applications
                    tunnelsData.forEach(tunnelData => {
                        const tunnel = this._createTunnelObject('quick', tunnelData.targetUrl, tunnelData.tunnelUrl);
                        this.quick_tunnels[tunnelData.targetUrl] = tunnel;
                    });
                    
                    return Object.values(this.quick_tunnels);
                } catch (error) {
                    if (attempt < MAX_RETRIES) {
                        await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
                    } else {
                        console.error('Error fetching quick tunnels:', error);
                        return [];
                    }
                }
            }
        },

        fetch: async function() {
            const namedPromise = this.fetchNamedTunnels();
            const quickPromise = this.fetchQuickTunnels();
            
            const [named, quick] = await Promise.all([namedPromise, quickPromise]);
            return {
                named: named,
                quick: quick
            };
        },

        // Create a new quick tunnel (public API method)
        createQuickTunnel: async function(targetUrl) {
            document.querySelector('.tunnel-loading').classList.add('show');
            try {
                const validUrl = window.app.url.validate(targetUrl); // Assuming this function exists
                const response = await fetch(`/start-quick-tunnel/${encodeURIComponent(validUrl)}`, {
                    method: 'POST'
                });
                
                if (!response.ok) {
                    throw new Error(`Failed to create tunnel: ${response.statusText}`);
                }
                
                const data = await response.json();

                // Create a tunnel object using the factory
                const newTunnel = this._createTunnelObject('quick', validUrl, data.tunnel_url);
                
                // Store the tunnel right away so UI can show "pending" status
                this.quick_tunnels[validUrl] = newTunnel;

                // Start checking tunnel status repeatedly until it becomes active or errors out
                this._waitForTunnelActive(newTunnel);
                
                return newTunnel;
            } catch (error) {
                console.error('Error creating tunnel:', error);
                return null;
            } finally {
                this.ui.renderTunnelTable();
                document.querySelector('.tunnel-loading').classList.remove('show');
            }
        },
        
        // Helper to wait for a tunnel to become active, with multiple attempts
        _waitForTunnelActive: async function(tunnel, maxAttempts = 10, interval = 1500) {
            let attempts = 0;
            
            const checkStatus = async () => {
                if (attempts >= maxAttempts) return;
                
                attempts++;
                const isActive = await tunnel.isActive();
                
                if (isActive) {
                    // Success - tunnel is active
                    // Tunnel is active
                } else if (tunnel.status === 'error' && attempts >= maxAttempts) {
                    // Failed after max attempts
                    console.error(`Tunnel ${tunnel.tunnelUrl} failed to activate after ${maxAttempts} attempts`);
                    window.app.showToast(`Tunnel couldn't be activated after ${maxAttempts} attempts`, 'error');
                } else {
                    // Still pending, try again
                    setTimeout(checkStatus, interval);
                }
            };
            
            // Start checking
            checkStatus();
        },

        // Method to stop a quick tunnel via API
        stopQuickTunnel: async function(targetUrl) {
            try {
                const validUrl = window.app.url.validate(targetUrl); // Assuming this function exists
                const response = await fetch(`/stop-quick-tunnel/${encodeURIComponent(validUrl)}`, {
                    method: 'POST'
                });
                
                if (!response.ok) {
                    throw new Error(`Failed to stop tunnel: ${response.statusText}`);
                }
                
                const data = await response.json();
                // Completely remove the tunnel from our tracking
                if (this.quick_tunnels[targetUrl]) {
                    delete this.quick_tunnels[targetUrl];
                }
                
                return data;
            } catch (error) {
                console.error('Error stopping tunnel:', error);
                return null;
            }
        },
        
        // Method to refresh a quick tunnel via API
        refreshQuickTunnel: async function(targetUrl) {
            document.querySelector('.tunnel-loading').classList.add('show');
            try {
                const validUrl = window.app.url.validate(targetUrl); // Assuming this function exists
                const response = await fetch(`/refresh-quick-tunnel/${encodeURIComponent(validUrl)}`, {
                    method: 'POST'
                });
                
                if (!response.ok) {
                    throw new Error(`Failed to refresh tunnel: ${response.statusText}`);
                }
                
                const data = await response.json();

                // Update the existing tunnel
                if (this.quick_tunnels[targetUrl]) {
                    this.quick_tunnels[targetUrl].tunnelUrl = data.tunnel_url;
                    this.quick_tunnels[targetUrl].status = 'pending';
                    await this.quick_tunnels[targetUrl].isActive();
                } else {
                    // Create a new tunnel object if it doesn't exist
                    const newTunnel = this._createTunnelObject('quick', targetUrl, data.tunnel_url);
                    await newTunnel.isActive();
                    this.quick_tunnels[targetUrl] = newTunnel;
                }
                
                return data;
            } catch (error) {
                console.error('Error refreshing tunnel:', error);
                return null;
            } finally {
                document.querySelector('.tunnel-loading').classList.remove('show');
            }
        },

        canResolve: async function(tunnel_url, max_duration = 5000, polling_interval = 1000) {
            const tunnel = this.findByTunnelUrl(tunnel_url);
            if (!tunnel) return false; // Added return value for this case
            
            const app_search = applications.findByTargetUrl(tunnel.targetUrl);
            const app = app_search.length ? app_search[0] : null;
            const isProxied = app && app.isProxied();
        
            return new Promise((resolve) => {
                const start_time = Date.now();
                let resolved = false;
                
                // Create timeout to stop trying after max_duration
                const timeout = setTimeout(() => {
                    if (!resolved) {
                        resolved = true;
                        resolve(false);
                    }
                }, max_duration);
                
                // Function to attempt using fetch
                const attemptLoad = async () => {
                    if (resolved || Date.now() - start_time >= max_duration) return;
                    
                    try {
                        // For non-proxied apps, we just need to know if we got any response (best effort)
                        // For proxied apps, we need a 200 response
                        if (isProxied) {
                            // When proxied, we need to check for a 200 response
                            // Using regular fetch (not no-cors) to read the status
                            const response = await fetch(`${tunnel_url}/portal-resolver?t=${Date.now()}`, {
                                cache: 'no-store',
                                credentials: 'omit'
                            });
                            
                            if (response.status === 200 && !resolved) {
                                clearTimeout(timeout);
                                resolved = true;
                                resolve(true);
                            } else if (!resolved && Date.now() - start_time < max_duration) {
                                // If not a 200 response and we have time, try again
                                setTimeout(attemptLoad, polling_interval);
                            } else if (!resolved) {
                                resolved = true;
                                resolve(false);
                            }
                        } else {
                            // For non-proxied apps where we cannot control CORS headers, we just need any response
                            // This may give us a false positive for resolvable but it is the best we can do
                            // Use fetch with no-cors
                            await fetch(`${tunnel_url}/portal-resolver?t=${Date.now()}`, {
                                mode: 'no-cors',
                                cache: 'no-store',
                                credentials: 'omit'
                            });
                            
                            // If we get here, we got a response (for non-proxied apps this is enough)
                            if (!resolved) {
                                clearTimeout(timeout);
                                resolved = true;
                                resolve(true);
                            }
                        }
                    } catch (fetchError) {
                        // If we still have time, try again
                        if (!resolved && Date.now() - start_time < max_duration) {
                            setTimeout(attemptLoad, polling_interval);
                        } else if (!resolved) {
                            resolved = true;
                            resolve(false);
                        }
                    }
                };
                
                // Start the first attempt
                attemptLoad();
            });
        },
        
        // Get all tunnels (both named and quick)
        getAllTunnels: function() {
            return {
                named: Object.values(this.named_tunnels),
                quick: Object.values(this.quick_tunnels)
            };
        },
        
        // Find a tunnel by its tunnel URL
        findByTunnelUrl: function(tunnelUrl) {
            // Search in named tunnels
            for (const targetUrl in this.named_tunnels) {
                if (this.named_tunnels[targetUrl].tunnelUrl === tunnelUrl) {
                    return this.named_tunnels[targetUrl];
                }
            }
            
            // Search in quick tunnels
            for (const targetUrl in this.quick_tunnels) {
                if (this.quick_tunnels[targetUrl].tunnelUrl === tunnelUrl) {
                    return this.quick_tunnels[targetUrl];
                }
            }
            
            return null;
        },
        
        // Initialize the tunnels object
        init: async function() {
            this.ui.parent = this;
            // Fetch existing tunnels first
            await this.fetch();
            
            // Check status of all tunnels
            await this.checkAllTunnelStatus();
            
            // Initialize the UI components
            this.ui.init(this);
            
            // Setup periodic status checks
            // This will create noise in the network tab
            this.startStatusChecks();
            
            return this;
        },
        
        // Check status of all tunnels
        checkAllTunnelStatus: async function() {
            const promises = [];
            
            // Check all named tunnels
            Object.values(this.named_tunnels).forEach(tunnel => {
                promises.push(tunnel.isActive());
            });
            
            // Check all quick tunnels
            Object.values(this.quick_tunnels).forEach(tunnel => {
                promises.push(tunnel.isActive());
            });
            
            // Wait for all status checks to complete
            await Promise.all(promises);
            
            return {
                named: Object.values(this.named_tunnels).map(t => t.status),
                quick: Object.values(this.quick_tunnels).map(t => t.status)
            };
        },
        
        // Start periodic status checks
        startStatusChecks: function(interval = 30000) {
            // Clear any existing interval
            if (this._statusCheckInterval) {
                clearInterval(this._statusCheckInterval);
            }

            // Setup new interval — check status and re-fetch tunnel list
            this._statusCheckInterval = setInterval(async () => {
                await this.fetch();
                const statusChanges = await this.checkAllTunnelStatus();
                this.ui.renderTunnelTable();
            }, interval);
        },
        
        // Tunnels UI subsystem
        ui: {
            // DOM element IDs
            elements: {
                tableBodyId: 'tunnel-table-body',
                targetUrlInput: 'target-url',
                createTunnelBtn: 'create-tunnel-btn',
                tunnelLoading: 'tunnel-loading',
                tunnelForm: 'tunnel-form'
            },
            
            // Custom events
            events: {
                tunnelCreated: new Event('tunnelCreated'),
                tunnelStopped: new Event('tunnelStopped'),
                tunnelRefreshed: new Event('tunnelRefreshed')
            },
            
            // Initialize UI components and listeners
            init: function(parentReference) {         
                this._parent = parentReference;

                // Initial table render
                this.renderTunnelTable();
                
                // Setup form event listeners
                this._setupFormListeners();
                
                // Listen for tunnel events to update UI
                document.addEventListener('tunnelCreated', () => this.renderTunnelTable());
                document.addEventListener('tunnelStopped', () => this.renderTunnelTable());
                document.addEventListener('tunnelRefreshed', () => this.renderTunnelTable());
            },
            
            // Setup form event listeners
            _setupFormListeners: function() {
                // Create tunnel form submission
                const createBtn = document.getElementById(this.elements.createTunnelBtn);
                if (createBtn) {
                    createBtn.addEventListener('click', (e) => {
                        e.preventDefault();
                        this.handleCreateTunnel();
                    });
                }
                
                // Allow hitting Enter in the input field to submit
                const targetInput = document.getElementById(this.elements.targetUrlInput);
                if (targetInput) {
                    targetInput.addEventListener('keypress', (e) => {
                        if (e.key === 'Enter') {
                            e.preventDefault();
                            this.handleCreateTunnel();
                        }
                    });
                }
            },
            
            // Handle tunnel creation from form
            handleCreateTunnel: async function() {
                const tunnelsRef = this._parent;

                const targetInput = document.getElementById(this.elements.targetUrlInput);
                if (!targetInput) return;
                
                const targetUrl = targetInput.value.trim();
                if (!targetUrl) {
                    window.app.showToast('Please enter a target URL', 'warning');
                    return;
                }
                
                // Display loading state
                this.showLoading(true);
                
                try {
                    // Validate URL
                    const validUrl = window.app.url.validate(targetUrl);
                    
                    // Create the tunnel
                    const newTunnel = await tunnelsRef.createQuickTunnel(validUrl);
                    
                    if (newTunnel) {
                        // Clear the input on success
                        targetInput.value = '';
                        window.app.showToast('Tunnel created successfully', 'success');
                    } else {
                        window.app.showToast('Failed to create tunnel', 'error');
                    }
                } catch (error) {
                    console.error('Error creating tunnel:', error);
                    window.app.showToast(error.message || 'Error creating tunnel', 'error');
                } finally {
                    // Hide loading state
                    this.showLoading(false);
                }
            },
            
            // Show/hide loading indicator
            showLoading: function(show) {
                const loader = document.querySelector(`.${this.elements.tunnelLoading}`);
                if (loader) {
                    if (show) {
                        loader.classList.add('show');
                    } else {
                        loader.classList.remove('show');
                    }
                }
            },
            
            // Toggle management options for a tunnel
            toggleManageOptions: function(targetUrl) {
                const allManageSections = document.querySelectorAll('.manage-section');
                const targetSection = document.querySelector(`.manage-section[data-target="${targetUrl}"]`);
                
                // Close all other sections first
                allManageSections.forEach(section => {
                    if (section !== targetSection && section.classList.contains('show')) {
                        section.classList.remove('show');
                    }
                });
                
                // Toggle the target section
                if (targetSection) {
                    targetSection.classList.toggle('show');
                }
            },
            
            // Render the tunnel table
            renderTunnelTable: function() {
                const tunnelsRef = this._parent;

                const tableBody = document.getElementById(this.elements.tableBodyId);
                if (!tableBody) {
                    console.error(`Tunnel table body with ID "${this.elements.tableBodyId}" not found`);
                    return;
                }
                tableBody.innerHTML = '';

                // Add named tunnels
                Object.values(tunnelsRef.named_tunnels).forEach(tunnel => {
                    const row = document.createElement('div');
                    row.classList.add("tunnel-item");
                    row.innerHTML = `
                        <div>${tunnel.targetUrl}</div>
                        <div>${tunnel.tunnelUrl}</div>
                        <div class="tunnel-actions">
                            <button class="copy-btn" data-url="${tunnel.tunnelUrl}">
                                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>
                                Copy URL
                            </button>
                        </div>
                    `;
                    this._addButtonListeners(row, tunnel);
                    tableBody.appendChild(row);
                });

                // Add quick tunnels
                Object.values(tunnelsRef.quick_tunnels).forEach(tunnel => {
                    const row = document.createElement('div');
                    row.classList.add("tunnel-item");
                    
                    row.innerHTML = `
                        <div>${tunnel.targetUrl}</div>
                        <div>${tunnel.tunnelUrl}</div>
                        <div class="tunnel-actions">
                            <button class="copy-btn" data-url="${tunnel.tunnelUrl}">
                                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>
                                Copy URL
                            </button>
                            <button class="manage-toggle" data-target="${tunnel.targetUrl}">
                                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon">
                                    <circle cx="12" cy="12" r="3"></circle>
                                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
                                </svg>
                                Manage
                            </button>
                        </div>
                        
                        <div class="manage-section" data-target="${tunnel.targetUrl}">                               
                            <div class="tunnel-management-actions">
                                <button class="small-btn secondary-btn refresh-btn" data-target="${tunnel.targetUrl}">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon"><path d="M23 4v6h-6"></path><path d="M1 20v-6h6"></path><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                                    Refresh Tunnel
                                </button>
                                <button class="small-btn secondary-btn stop-btn" data-target="${tunnel.targetUrl}">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect></svg>
                                    Stop Tunnel
                                </button>
                            </div>
                        </div>
                    `;
                    this._addButtonListeners(row, tunnel);
                    tableBody.appendChild(row);
                });
                
                // Add a message if no tunnels exist
                if (Object.keys(tunnelsRef.named_tunnels).length === 0 && 
                    Object.keys(tunnelsRef.quick_tunnels).length === 0) {
                    const emptyRow = document.createElement('div');
                    emptyRow.classList.add("tunnel-item");
                    emptyRow.innerHTML = `
                        <div colspan="3">No active tunnels. Create a new tunnel to get started.</div>
                    `;
                    tableBody.appendChild(emptyRow);
                }
                
                // Update application UI with new tunnel info
                if (typeof applications !== 'undefined' && applications.updateAllUI) {
                    applications.updateAllUI();
                }
            },
            
            // Add event listeners to buttons in a row
            _addButtonListeners: function(row, tunnel) {
                // Copy button
                const copyBtn = row.querySelector('.copy-btn');
                if (copyBtn) {
                    copyBtn.addEventListener('click', () => {
                        const url = copyBtn.getAttribute('data-url');
                        window.app.url.copy(url);
                    });
                }
                
                // Manage toggle button for quick tunnels
                const manageToggle = row.querySelector('.manage-toggle');
                if (manageToggle) {
                    manageToggle.addEventListener('click', () => {
                        const targetUrl = manageToggle.getAttribute('data-target');
                        this.toggleManageOptions(targetUrl);
                    });
                }
                
                // Refresh button (quick tunnels only)
                const refreshBtn = row.querySelector('.refresh-btn');
                if (refreshBtn) {
                    refreshBtn.addEventListener('click', async () => {
                        const targetUrl = refreshBtn.getAttribute('data-target');
                        try {
                            refreshBtn.disabled = true;
                            this.showLoading(true);
                            const success = await tunnel.refresh();
                            if (success) {
                                window.app.showToast('Tunnel refreshed successfully');
                                document.dispatchEvent(this.events.tunnelRefreshed);
                            } else {
                                window.app.showToast('Failed to refresh tunnel', 'error');
                            }
                        } catch (error) {
                            console.error('Error refreshing tunnel:', error);
                            window.app.showToast('Error refreshing tunnel', 'error');
                        } finally {
                            refreshBtn.disabled = false;
                            this.showLoading(false);
                        }
                    });
                }
                
                // Stop button (quick tunnels only)
                const stopBtn = row.querySelector('.stop-btn');
                if (stopBtn) {
                    stopBtn.addEventListener('click', async () => {
                        const targetUrl = stopBtn.getAttribute('data-target');
                        
                        try {
                            stopBtn.disabled = true;
                            const success = await tunnel.stop();
                            if (success) {
                                window.app.showToast('Tunnel stopped successfully');
                                document.dispatchEvent(this.events.tunnelStopped);
                            } else {
                                window.app.showToast('Failed to stop tunnel', 'error');
                            }
                        } catch (error) {
                            console.error('Error stopping tunnel:', error);
                            window.app.showToast('Error stopping tunnel', 'error');
                        } finally {
                            stopBtn.disabled = false;
                        }
                    });
                }
            },
        }
    };

    // ─── Services (Supervisor Process Management) ────────────────────
    const services = {
        _data: [],
        _pollInterval: null,
        _pending: {},  // name -> action being performed

        fetch: async function() {
            try {
                const response = await fetch('/supervisor/processes');
                if (!response.ok) return;
                this._data = await response.json();
            } catch (e) {
                console.warn('Supervisor not available:', e.message);
            }
        },

        action: async function(name, action) {
            this._pending[name] = action;
            this.ui.render();
            try {
                const response = await fetch(`/supervisor/process/${encodeURIComponent(name)}/${action}`, {
                    method: 'POST'
                });
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || `Failed to ${action} ${name}`);
                }
                // If restarting the portal itself, refresh so the 503 handler shows the placeholder
                if (name === 'instance_portal' && action === 'restart') {
                    setTimeout(() => window.location.reload(), 1000);
                    return;
                }
                window.app.showToast(`${name}: ${action} successful`, 'success');
            } catch (e) {
                window.app.showToast(e.message, 'error');
            } finally {
                delete this._pending[name];
                // Refresh after a short delay to let supervisor settle
                setTimeout(() => this.refresh(), 500);
            }
        },

        start: function(name) { return this.action(name, 'start'); },
        stop: function(name) { return this.action(name, 'stop'); },
        restart: function(name) { return this.action(name, 'restart'); },

        // Match supervisor process names to portal app names
        // e.g. "jupyter" matches "Jupyter", "instance_portal" matches "Instance Portal"
        _matchesApp: function(procName, appName) {
            const normalized = procName.replace(/_/g, ' ').toLowerCase();
            return appName.toLowerCase() === normalized ||
                   appName.toLowerCase().includes(normalized) ||
                   normalized.includes(appName.toLowerCase());
        },

        getAppState: function(appName) {
            for (const proc of this._data) {
                if (this._matchesApp(proc.name, appName)) {
                    return proc.state;
                }
            }
            return null;
        },

        updateAppCards: function() {
            if (!this._data.length) return;
            const appGrid = document.getElementById('app-grid');
            if (!appGrid) return;
            const cards = Array.from(appGrid.querySelectorAll('.card[data-app-id]'));
            let needsReorder = false;

            cards.forEach(card => {
                const appName = card.getAttribute('data-app-id');
                const state = this.getAppState(appName);
                const isStopped = state && state !== 'RUNNING' && state !== 'STARTING';
                const wasStopped = card.classList.contains('service-stopped');

                if (isStopped && !wasStopped) needsReorder = true;
                if (!isStopped && wasStopped) needsReorder = true;

                if (isStopped) {
                    card.classList.add('service-stopped');
                    const btn = card.querySelector('.launch-btn');
                    if (btn && !btn.hasAttribute('data-original-text')) {
                        btn.setAttribute('data-original-text', btn.textContent.trim());
                        btn.textContent = 'Service Stopped';
                        btn.disabled = true;
                    }
                } else {
                    card.classList.remove('service-stopped');
                    const btn = card.querySelector('.launch-btn');
                    if (btn && btn.hasAttribute('data-original-text')) {
                        btn.textContent = btn.getAttribute('data-original-text');
                        btn.removeAttribute('data-original-text');
                        // Don't re-enable Instance Portal's button
                        if (appName !== 'Instance Portal') btn.disabled = false;
                    }
                }
            });

            // Move stopped cards to end of grid
            if (needsReorder) {
                const running = [];
                const stopped = [];
                cards.forEach(card => {
                    if (card.classList.contains('service-stopped')) {
                        stopped.push(card);
                    } else {
                        running.push(card);
                    }
                });
                running.concat(stopped).forEach(card => appGrid.appendChild(card));
            }
        },

        refresh: async function() {
            await this.fetch();
            this.ui.render();
            this.updateAppCards();
        },

        init: async function() {
            await this.fetch();
            this.ui.render();
            this.updateAppCards();
            this._pollInterval = setInterval(() => this.refresh(), 10000);
        },

        ui: {
            _formatUptime: function(startTs, nowTs) {
                if (!startTs || startTs === 0) return '';
                const secs = nowTs - startTs;
                if (secs < 60) return `${secs}s`;
                if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
                const h = Math.floor(secs / 3600);
                const m = Math.floor((secs % 3600) / 60);
                return `${h}h ${m}m`;
            },

            _spinnerSvg: '<svg class="service-spinner" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>',

            render: function() {
                const tbody = document.getElementById('services-table-body');
                if (!tbody) return;

                if (services._data.length === 0) {
                    tbody.innerHTML = '<div class="service-item" style="justify-content: center; color: var(--text-secondary);">No supervisor processes found</div>';
                    return;
                }

                // Sort: running first, then fatal/backoff, then stopped/exited last
                const stateOrder = (s) => {
                    if (s === 'RUNNING' || s === 'STARTING') return 0;
                    if (s === 'FATAL' || s === 'BACKOFF') return 1;
                    return 2; // STOPPED, EXITED, STOPPING, UNKNOWN
                };
                const sorted = [...services._data].sort((a, b) => {
                    const diff = stateOrder(a.state) - stateOrder(b.state);
                    if (diff !== 0) return diff;
                    return a.name.localeCompare(b.name);
                });

                tbody.innerHTML = sorted.map(proc => {
                    const pending = services._pending[proc.name];
                    const stateClass = pending ? 'starting' : proc.state.toLowerCase();
                    const pendingLabels = {start: 'STARTING', stop: 'STOPPING', restart: 'RESTARTING'};
                    const stateLabel = pending ? (pendingLabels[pending] || pending.toUpperCase()) : proc.state;
                    const isRunning = proc.state === 'RUNNING';
                    const uptime = isRunning ? this._formatUptime(proc.start, proc.now) : '';
                    const info = isRunning && proc.pid ? `PID ${proc.pid}` + (uptime ? ` · ${uptime}` : '') : '';

                    // Determine which buttons to show
                    // Disable all buttons when process is in a transitional state
                    const transitional = pending || ['STARTING', 'STOPPING', 'BACKOFF'].includes(proc.state);
                    let actions = '';
                    if (transitional) {
                        const label = pending ? (pendingLabels[pending] || pending.toUpperCase()) : proc.state;
                        actions = `<button class="service-btn" disabled>${this._spinnerSvg} ${label}</button>`;
                    } else if (isRunning) {
                        const safeName = escapeAttr(proc.name);
                    const stopBtn = proc.unstoppable ? '' : `
                            <button class="service-btn danger" onclick="window.app.services.stop('${safeName}')" title="Stop">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>
                                Stop</button>`;
                        actions = `
                            <button class="service-btn" onclick="window.app.services.restart('${safeName}')" title="Restart">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                                Restart</button>${stopBtn}`;
                    } else {
                        actions = `
                            <button class="service-btn" onclick="window.app.services.start('${safeName}')" title="Start">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                                Start</button>`;
                    }

                    return `<div class="service-item">
                        <div class="service-name">${escapeHtml(proc.name)}</div>
                        <div><span class="status-badge ${escapeAttr(stateClass)}">${escapeHtml(stateLabel)}</span></div>
                        <div class="service-info">${escapeHtml(info)}</div>
                        <div class="service-actions">${actions}</div>
                    </div>`;
                }).join('');
            }
        }
    };

    const systemMetrics = {
        // DOM element IDs
        elements: {
            gpuFill: 'gpu-fill',
            gpuTooltip: 'gpu-tooltip',
            cpuFill: 'cpu-fill',
            cpuTooltip: 'cpu-tooltip',
            ramFill: 'ram-fill',
            ramTooltip: 'ram-tooltip',
            diskFill: 'disk-fill',
            diskTooltip: 'disk-tooltip',
            volumeStat: 'volume-stat',
            volumeFill: 'volume-fill',
            volumeTooltip: 'volume-tooltip'
        },

        // Data storage
        data: {
            gpu: null,
            cpu: null,
            ram: null,
            disk: null,
            volume: null
        },
        
        // Fetch metrics from API
        fetch: async function() {
            try {
                const response = await fetch('/system-metrics');
                
                if (!response.ok) {
                    throw new Error('Failed to fetch system metrics');
                }
                
                const data = await response.json();
                this.data = data;
                return data;
            } catch (error) {
                console.error('Error fetching system metrics:', error);
                return null;
            }
        },
        
        // Update UI with latest metrics
        updateUI: function() {
            // Update GPU metrics
            if (this.data.gpu) {
                const gpuLoad = this.data.gpu.avg_load_percent;
                const gpuMemoryUsed = this.data.gpu.memory_used / 1024; // Convert to GB
                const gpuMemoryTotal = this.data.gpu.memory_total / 1024; // Convert to GB
                
                const gpuEl = document.getElementById(this.elements.gpuFill);
                gpuEl.style.width = `${gpuLoad}%`;
                gpuEl.setAttribute('data-level', gpuLoad >= 90 ? 'critical' : gpuLoad >= 80 ? 'warning' : 'good');
                document.getElementById(this.elements.gpuTooltip).textContent =
                    `Load: ${Math.round(gpuLoad)}% | Memory: ${gpuMemoryUsed.toFixed(1)}/${gpuMemoryTotal.toFixed(1)} GB`;
            }
            
            // Update CPU metrics
            if (this.data.cpu) {
                const cpuPercent = this.data.cpu.percent;
                const cpuCount = this.data.cpu.count;

                const cpuEl = document.getElementById(this.elements.cpuFill);
                cpuEl.style.width = `${cpuPercent}%`;
                cpuEl.setAttribute('data-level', cpuPercent >= 90 ? 'critical' : cpuPercent >= 80 ? 'warning' : 'good');
                document.getElementById(this.elements.cpuTooltip).textContent =
                    `${cpuPercent.toFixed(1)}% (${cpuCount} core${cpuCount !== 1 ? 's' : ''})`;
            }

            // Update RAM metrics
            if (this.data.ram) {
                const ramPercent = this.data.ram.percent;
                const ramUsed = this.data.ram.used / 1e9; // Convert to GB (decimal)
                const ramTotal = this.data.ram.total / 1e9;

                const ramEl = document.getElementById(this.elements.ramFill);
                ramEl.style.width = `${ramPercent}%`;
                ramEl.setAttribute('data-level', ramPercent >= 90 ? 'critical' : ramPercent >= 80 ? 'warning' : 'good');
                document.getElementById(this.elements.ramTooltip).textContent =
                    `${ramUsed.toFixed(1)}/${ramTotal.toFixed(1)} GB (${ramPercent.toFixed(1)}%)`;
            }

            // Update Disk metrics
            if (this.data.disk) {
                const diskPercent = this.data.disk.percent;
                const diskUsed = this.data.disk.used / (1024 * 1024 * 1024); // GiB to match df -h
                const diskTotal = this.data.disk.total / (1024 * 1024 * 1024);

                const diskEl = document.getElementById(this.elements.diskFill);
                diskEl.style.width = `${diskPercent}%`;
                diskEl.setAttribute('data-level', diskPercent >= 90 ? 'critical' : diskPercent >= 80 ? 'warning' : 'good');
                document.getElementById(this.elements.diskTooltip).textContent =
                    `${Math.round(diskUsed)}/${Math.round(diskTotal)} GB (${diskPercent.toFixed(1)}%)`;
            }

            // Adapt UI based on environment (container vs VM)
            if (this.data.environment) {
                const isVM = this.data.environment !== 'container';
                // Hide supervisor tab in VM mode (no supervisor available)
                const servicesNav = document.querySelector('[data-page="services-page"]');
                if (servicesNav) servicesNav.style.display = isVM ? 'none' : '';
                // Rename "Container Disk" to "Disk" in VM mode
                const diskLabel = document.querySelector('#disk-fill')?.closest('.stat-item')?.querySelector('.stat-label');
                if (diskLabel) diskLabel.textContent = isVM ? 'Disk' : 'Container Disk';
            }

            // Update Volume metrics (shown only when $WORKSPACE is a mounted volume)
            const volumeStatEl = document.getElementById(this.elements.volumeStat);
            if (this.data.volume) {
                volumeStatEl.style.display = '';
                const volPercent = this.data.volume.percent;
                const volUsed = this.data.volume.used / (1024 * 1024 * 1024); // GiB to match df -h
                const volTotal = this.data.volume.total / (1024 * 1024 * 1024);

                const volEl = document.getElementById(this.elements.volumeFill);
                volEl.style.width = `${volPercent}%`;
                volEl.setAttribute('data-level', volPercent >= 90 ? 'critical' : volPercent >= 80 ? 'warning' : 'good');
                document.getElementById(this.elements.volumeTooltip).textContent =
                    `${Math.round(volUsed)}/${Math.round(volTotal)} GB (${volPercent.toFixed(1)}%)`;
            } else if (volumeStatEl) {
                volumeStatEl.style.display = 'none';
            }
        },
        
        // Start periodic updates
        startUpdates: async function(interval = 5000) {
            // Clear any existing interval
            if (this._updateInterval) {
                clearInterval(this._updateInterval);
            }
            
            // Initial update
            await this.fetch();
            this.updateUI();
            
            // Set up interval for periodic updates
            this._updateInterval = setInterval(async () => {
                await this.fetch();
                this.updateUI();
            }, interval);
        },

        
        // Stop periodic updates
        stopUpdates: function() {
            if (this._updateInterval) {
                clearInterval(this._updateInterval);
                this._updateInterval = null;
            }
        },
        
        // Initialize the system metrics module
        init: function() {
            this.startUpdates();
            return this;
        }
    };
    
    const logManager = {
        // DOM element IDs
        elements: {
            logConsole: 'log-console',
            pauseButton: 'pause-button',
            copyButton: 'copyLogsBtn',
            downloadButton: 'downloadLogsBtn'
        },
        
        // Connection state
        isPaused: false,
        webSocket: null,
        maxLogLines: 300,
        lastSpansByFile: {},  // file -> [span, ...] array for per-file overwrite tracking
        
        // Connection management
        reconnectTimer: null,
        heartbeatTimer: null,
        connectionCheckTimer: null,
        lastHeartbeat: 0,
        reconnectAttempts: 0,
        maxReconnectAttempts: 10,
        
        updateConnectionStatus: function(status) {
            const dot = document.getElementById('ws-status');
            if (!dot) return;
            dot.className = 'ws-status-dot';
            if (status === 'connected') {
                dot.classList.add('connected');
                dot.title = 'Connected';
            } else if (status === 'reconnecting') {
                dot.classList.add('reconnecting');
                dot.title = 'Reconnecting...';
            } else {
                dot.title = 'Disconnected';
            }
        },

        // Toggle pause state
        togglePause: function() {
            this.isPaused = !this.isPaused;
            
            // Update pause button icon
            const pauseIcon = `
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="6" y="4" width="4" height="16" rx="1" />
                    <rect x="14" y="4" width="4" height="16" rx="1" />
                </svg>
            `;
            const playIcon = `
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M5 4.99v14.02c0 1.1 1.23 1.75 2.15 1.14l12.46-7.01a1.4 1.4 0 0 0 0-2.28L7.15 3.85C6.23 3.24 5 3.89 5 4.99z"/>
                </svg>
            `;
            
            const pauseBtn = document.getElementById(this.elements.pauseButton);
            if (pauseBtn) {
                pauseBtn.innerHTML = this.isPaused ? playIcon : pauseIcon;
            }
            
            // Scroll to bottom if unpausing
            if (!this.isPaused) {
                this.scrollToBottom();
            }
        },
        
        // Scroll to bottom of logs
        scrollToBottom: function() {
            if (!this.isPaused) {
                const logConsole = document.getElementById(this.elements.logConsole);
                if (logConsole) {
                    logConsole.scrollTop = logConsole.scrollHeight;
                }
            }
        },
        
        // Copy logs to clipboard
        copyLogs: async function() {
            const copyBtn = document.getElementById(this.elements.copyButton);
            if (!copyBtn) return;
    
            const logConsole = document.getElementById(this.elements.logConsole);
            if (!logConsole) return;
            
            try {
                await navigator.clipboard.writeText(logConsole.textContent);
                window.app.showToast('Logs copied to clipboard');
            } catch (err) {
                console.error('Failed to copy logs:', err);
                window.app.showToast('Failed to copy logs', 'error');
            }
        },
        
        // Download logs from the server
        downloadLogs: async function() {
            const downloadBtn = document.getElementById(this.elements.downloadButton);
            if (!downloadBtn) return;
            
            try {
                // Change button state to show loading
                downloadBtn.disabled = true;
                downloadBtn.textContent = 'Preparing Logs...';
                
                // Get instance ID from page element
                const instanceId = document.querySelector('.instance-id').textContent.split(': ')[1];
                
                // Generate timestamp in the specified format
                const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
                
                // Create filename with instance ID and timestamp as specified
                const filename = `${instanceId}_logs_${timestamp}.zip`;
                
                // Call the FastAPI endpoint with the filename as a query parameter
                const response = await fetch(`/download-logs?filename=${encodeURIComponent(filename)}`);
                
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || 'Failed to download logs');
                }
                
                // Convert the response to a blob
                const blob = await response.blob();
                
                // Create a temporary URL for the blob
                const url = window.URL.createObjectURL(blob);
                
                // Create a temporary link element to trigger the download
                const link = document.createElement('a');
                link.href = url;
                link.download = filename;
                document.body.appendChild(link);
                
                // Trigger the download
                link.click();
                
                // Clean up
                window.URL.revokeObjectURL(url);
                document.body.removeChild(link);
                window.app.showToast('Logs downloaded successfully');
                
            } catch (error) {
                console.error('Error downloading logs:', error);
                window.app.showToast(`Error downloading logs: ${error.message}`, 'error');
            } finally {
                // Reset button state
                downloadBtn.disabled = false;
                downloadBtn.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                        Download Logs
                `;
            }
        },
        
        // Collect all actively-tracked spans (progress bars, blocks)
        _getActiveSpans: function() {
            const active = new Set();
            for (const file in this.lastSpansByFile) {
                const spans = this.lastSpansByFile[file];
                if (spans) {
                    for (const s of spans) active.add(s);
                }
            }
            return active;
        },

        // Trim log console to maxLogLines, skipping actively-tracked spans
        // (progress bars / blocks). Active spans are pinned at the bottom.
        _trimLog: function(logConsole) {
            const excess = logConsole.childElementCount - this.maxLogLines;
            if (excess <= 0) return;
            const active = this._getActiveSpans();
            let removed = 0;
            // Limit iterations to avoid infinite loops if all spans are active
            let maxIter = excess + active.size;
            while (logConsole.childElementCount > this.maxLogLines && maxIter-- > 0) {
                const first = logConsole.firstChild;
                if (!first) break;
                if (active.has(first)) {
                    // Pin: move active span to the bottom instead of removing
                    logConsole.appendChild(first);
                } else {
                    logConsole.removeChild(first);
                    removed++;
                }
            }
        },

        // Append log message to the console (new line)
        appendLog: function(html, file) {
            if (this.isPaused) return;

            const logConsole = document.getElementById(this.elements.logConsole);
            if (!logConsole) return;

            const span = document.createElement('span');
            span.innerHTML = html;
            logConsole.appendChild(span);
            if (file) this.lastSpansByFile[file] = [span];

            this._trimLog(logConsole);
            this.scrollToBottom();
        },

        // Overwrite the last span for a specific file (progress bar update)
        overwriteLog: function(html, file) {
            if (this.isPaused) return;

            const logConsole = document.getElementById(this.elements.logConsole);
            if (!logConsole) return;

            const spans = file ? this.lastSpansByFile[file] : null;
            const target = spans && spans.length > 0 ? spans[spans.length - 1] : null;
            if (target && target.parentNode === logConsole) {
                target.innerHTML = html;
            } else {
                this.appendLog(html, file);
                return;
            }

            this.scrollToBottom();
        },

        // Overwrite a multi-line block for a specific file (nested progress bars)
        overwriteBlock: function(htmlLines, file) {
            if (this.isPaused) return;
            const logConsole = document.getElementById(this.elements.logConsole);
            if (!logConsole) return;

            const existing = this.lastSpansByFile[file];
            if (existing && existing.length > 0 && existing[0].parentNode === logConsole) {
                // Shrink excess spans
                while (existing.length > htmlLines.length) {
                    logConsole.removeChild(existing.pop());
                }
                // Update existing spans
                for (let i = 0; i < existing.length; i++) {
                    existing[i].innerHTML = htmlLines[i];
                }
                // Grow: add new spans after last existing
                const anchor = existing[existing.length - 1].nextSibling;
                while (existing.length < htmlLines.length) {
                    const span = document.createElement('span');
                    span.innerHTML = htmlLines[existing.length];
                    logConsole.insertBefore(span, anchor);
                    existing.push(span);
                }
            } else {
                // Create fresh block
                const spans = htmlLines.map(html => {
                    const span = document.createElement('span');
                    span.innerHTML = html;
                    logConsole.appendChild(span);
                    return span;
                });
                this.lastSpansByFile[file] = spans;
            }

            this._trimLog(logConsole);
            this.scrollToBottom();
        },

        appendSystemMessage: function(html) {
            if (this.isPaused) return;

            const logConsole = document.getElementById(this.elements.logConsole);
            if (!logConsole) return;

            const temp = document.createElement('div');
            temp.innerHTML = html;
            if (temp.firstChild) {
                logConsole.appendChild(temp.firstChild);
            }

            this._trimLog(logConsole);
            this.scrollToBottom();
        },
        
        // Setup connection monitoring
        setupConnectionMonitoring: function() {
            // Clear existing timers
            this.clearConnectionMonitoring();
            
            // Update last heartbeat time
            this.lastHeartbeat = Date.now();
            
            // Send regular pings to the server
            this.heartbeatTimer = setInterval(() => {
                if (this.webSocket && this.webSocket.readyState === WebSocket.OPEN) {
                    try {
                        this.webSocket.send("ping");
                    } catch (error) {
                        console.error("Error sending ping:", error);
                    }
                }
            }, 5000); // Every 5 seconds
            
            // Check if we're still receiving heartbeats
            this.connectionCheckTimer = setInterval(() => {
                if (!this.webSocket || this.webSocket.readyState !== WebSocket.OPEN) {
                    return; // No need to check if not connected
                }
                
                const elapsed = Date.now() - this.lastHeartbeat;
                
                // If no heartbeat in 30 seconds, connection is stale
                if (elapsed > 30000) {
                    this.updateConnectionStatus('reconnecting');
                    console.warn(`No heartbeat for ${elapsed/1000}s, reconnecting...`);

                    // Add visual indicator
                    this.appendSystemMessage(`<div style="color:orange;text-align:center;font-style:italic;margin:5px 0;border-bottom:1px dotted #ccc;">Connection stale, reconnecting...</div>`);
                    
                    // Force reconnection
                    this.reconnect();
                }
            }, 5000); // Check every 5 seconds
        },
        
        // Clear connection monitoring timers
        clearConnectionMonitoring: function() {
            if (this.heartbeatTimer) {
                clearInterval(this.heartbeatTimer);
                this.heartbeatTimer = null;
            }
            
            if (this.connectionCheckTimer) {
                clearInterval(this.connectionCheckTimer);
                this.connectionCheckTimer = null;
            }
        },
        
        // Connect to the WebSocket
        connect: function() {
            // Close any existing connection
            this.disconnect();
            
            try {
                // Calculate protocol (wss:// for https, ws:// for http)
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const host = window.location.host;
                
                // Add cache busting to prevent stale connections
                const timestamp = Date.now();
                const wsUrl = `${protocol}//${host}/ws-logs?_=${timestamp}`;
                
                // Create WebSocket connection
                this.webSocket = new WebSocket(wsUrl);
                
                // Use binary type arraybuffer for more stability
                this.webSocket.binaryType = 'arraybuffer';
                
                // Connection opened
                this.webSocket.addEventListener('open', (event) => {
                    this.updateConnectionStatus('connected');
                    this.lastHeartbeat = Date.now();
                    this.reconnectAttempts = 0;
                    
                    // Set up connection monitoring
                    this.setupConnectionMonitoring();
                    
                    // Add a system message
                    const now = new Date().toLocaleTimeString();
                    this.appendSystemMessage(`<div style="color:green;text-align:center;font-style:italic;margin:5px 0;border-bottom:1px dotted #ccc;">WebSocket connected at ${now}</div>`);
                });
                
                // Listen for messages
                this.webSocket.addEventListener('message', (event) => {
                    // Update heartbeat time for any message
                    this.lastHeartbeat = Date.now();
                    
                    const data = event.data;
                    
                    // Handle heartbeat message
                    if (data === 'heartbeat') {
                        return;
                    }
                    
                    // Handle pong message
                    if (data === 'pong') {
                        return;
                    }
                    
                    // Handle regular log messages
                    if (!this.isPaused && data) {
                        try {
                            const msg = JSON.parse(data);
                            const file = msg.file || '';
                            if (msg.type === 'overwrite_block') {
                                this.overwriteBlock(msg.lines, file);
                            } else if (msg.type === 'overwrite') {
                                this.overwriteLog(msg.html, file);
                            } else if (msg.type === 'system') {
                                this.appendSystemMessage(msg.html);
                            } else {
                                this.appendLog(msg.html, file);
                            }
                        } catch (e) {
                            // Fallback for non-JSON messages (legacy)
                            this.appendLog(data, '');
                        }
                    }
                });
                
                // Connection closed
                this.webSocket.addEventListener('close', (event) => {
                    this.updateConnectionStatus('disconnected');

                    // Clean up
                    this.clearConnectionMonitoring();
                    
                    // Add visual indicator
                    const now = new Date().toLocaleTimeString();
                    this.appendSystemMessage(`<div style="color:orange;text-align:center;font-style:italic;margin:5px 0;border-bottom:1px dotted #ccc;">Connection closed at ${now}</div>`);
                    
                    // Schedule reconnection
                    this.scheduleReconnect();
                });
                
                // Connection error
                this.webSocket.addEventListener('error', (error) => {
                    this.updateConnectionStatus('disconnected');
                    console.error('WebSocket error:', error);

                    // Add visual indicator
                    const now = new Date().toLocaleTimeString();
                    this.appendSystemMessage(`<div style="color:red;text-align:center;font-style:italic;margin:5px 0;border-bottom:1px dotted #ccc;">Connection error at ${now}</div>`);
                    
                    // Error is followed by close event which will handle reconnection
                });
                
            } catch (error) {
                console.error('Failed to create WebSocket:', error);
                this.appendSystemMessage(`<div style="color:red;text-align:center;font-style:italic;margin:5px 0;border-bottom:1px dotted #ccc;">Failed to create WebSocket: ${error.message}</div>`);
                this.scheduleReconnect();
            }
        },
        
        // Immediate reconnect
        reconnect: function() {
            this.disconnect();
            this.updateConnectionStatus('reconnecting');
            setTimeout(() => this.connect(), 100); // Small delay to ensure clean disconnect
        },
        
        // Schedule reconnect with exponential backoff
        scheduleReconnect: function() {
            if (this.reconnectTimer) {
                clearTimeout(this.reconnectTimer);
                this.reconnectTimer = null;
            }
            
            // Check max reconnect attempts
            if (this.reconnectAttempts >= this.maxReconnectAttempts) {
                console.error(`Maximum reconnect attempts reached (${this.maxReconnectAttempts})`);
                this.appendSystemMessage(`<div style="color:red;text-align:center;font-style:italic;margin:5px 0;border-bottom:1px dotted #ccc;">Maximum reconnect attempts reached. Please refresh the page.</div>`);
                return;
            }
            
            // Calculate backoff delay with jitter
            const baseDelay = Math.min(30000, 1000 * Math.pow(1.5, this.reconnectAttempts));
            const jitter = 0.85 + (Math.random() * 0.3); // 0.85-1.15 randomization
            const delay = Math.floor(baseDelay * jitter);
            
            this.reconnectAttempts++;
            this.updateConnectionStatus('reconnecting');
            
            // Schedule reconnection
            this.reconnectTimer = setTimeout(() => {
                this.connect();
            }, delay);
        },
        
        // Disconnect WebSocket
        disconnect: function() {
            // Clear monitoring
            this.clearConnectionMonitoring();
            
            // Clear reconnect timer
            if (this.reconnectTimer) {
                clearTimeout(this.reconnectTimer);
                this.reconnectTimer = null;
            }
            
            // Close the connection
            if (this.webSocket) {
                try {
                    // Only close if it's open or connecting
                    if (this.webSocket.readyState === WebSocket.OPEN || 
                        this.webSocket.readyState === WebSocket.CONNECTING) {
                        this.webSocket.close(1000, "Client disconnected");
                    }
                } catch (error) {
                    console.error('Error closing WebSocket:', error);
                }
                this.webSocket = null;
            }
        },
        
        // Set up event listeners
        setupEventListeners: function() {
            // Set up pause button
            const pauseBtn = document.getElementById(this.elements.pauseButton);
            if (pauseBtn) {
                pauseBtn.addEventListener('click', () => this.togglePause());
            }
            
            // Set up copy button
            const copyBtn = document.getElementById(this.elements.copyButton);
            if (copyBtn) {
                copyBtn.addEventListener('click', () => this.copyLogs());
            }
    
            // Set up download button
            const downloadBtn = document.getElementById(this.elements.downloadButton);
            if (downloadBtn) {
                downloadBtn.addEventListener('click', () => this.downloadLogs());
            }
            
            // Handle visibility change to reconnect when tab becomes visible
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible') {
                    
                    // Check if we need to reconnect
                    const stale = !this.webSocket || 
                                  this.webSocket.readyState !== WebSocket.OPEN ||
                                  (Date.now() - this.lastHeartbeat > 10000);
                    
                    if (stale) {
                        this.reconnect();
                    }
                }
            });
            
            // Clean up on page unload
            window.addEventListener('beforeunload', () => {
                this.disconnect();
            });
        },
        
        // Initialize the log manager
        init: function() {
            this.setupEventListeners();
            this.connect();
            return this;
        }
    };
    
    // Main application UI controller
    const appUI = {
        _loaderTextTimeoutId: null,
        _skipWaiting: false,
        // Toast notification system
        toast: {
            container: null,
            timeout: null,
            duration: 3000, // Default duration in ms
            
            // Create toast container if it doesn't exist
            createContainer: function() {
                if (!this.container) {
                    this.container = document.createElement('div');
                    this.container.className = 'toast-container';
                    document.body.appendChild(this.container);
                }
            },
            
            // Show a toast notification
            show: function(message, type = 'success') {
                this.createContainer();
                
                // Create toast element
                const toast = document.createElement('div');
                toast.className = `toast ${type}`;
                
                // Icon based on type
                let icon = '';
                switch (type) {
                    case 'success':
                        icon = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="toast-icon"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
                        break;
                    case 'error':
                        icon = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="toast-icon"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>`;
                        break;
                    case 'warning':
                        icon = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="toast-icon"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`;
                        break;
                }
                
                toast.innerHTML = `${icon}<span>${message}</span>`;
                this.container.appendChild(toast);
                
                // Trigger animation on next tick
                setTimeout(() => {
                    toast.classList.add('show');
                }, 10);
                
                // Auto remove after duration
                setTimeout(() => {
                    toast.classList.remove('show');
                    // Remove element after transition completes
                    setTimeout(() => {
                        this.container.removeChild(toast);
                    }, 300);
                }, this.duration);
            }
        },
        
        // URL utility functions
        url: {
            // Copy URL to clipboard
            copy: async function(url) {
                try {
                    // Try the modern Clipboard API first (secure contexts)
                    if (navigator.clipboard && window.isSecureContext) {
                        await navigator.clipboard.writeText(url);
                        appUI.showToast('URL copied to clipboard');
                        return true;
                    } else {
                        // Fallback for HTTP or browsers without Clipboard API
                        const textArea = document.createElement('textarea');
                        textArea.value = url;
                        
                        // Position off-screen
                        textArea.style.position = 'fixed';
                        textArea.style.left = '-999999px';
                        textArea.style.top = '-999999px';
                        document.body.appendChild(textArea);
                        
                        // Select and copy
                        textArea.focus();
                        textArea.select();
                        
                        const successful = document.execCommand('copy');
                        document.body.removeChild(textArea);
                        
                        if (!successful) {
                            throw new Error('execCommand copy failed');
                        }
                        
                        appUI.showToast('URL copied to clipboard');
                        return true;
                    }
                } catch (err) {
                    console.error('Failed to copy URL:', err);
                    appUI.showToast('Failed to copy URL', 'error');
                    return false;
                }
            },
            
            // Validate and format URL
            validate: function(url) {
                if (!url) {
                    throw new Error('URL not defined');
                }
                let validUrl = url.trim();
                
                // Add protocol if missing
                if (!validUrl.startsWith('http://') && !validUrl.startsWith('https://')) {
                    validUrl = "http://" + validUrl;
                }
                
                return validUrl;
            }
        },
        
        // Show a toast notification (shorthand)
        showToast: function(message, type = 'success') {
            this.toast.show(message, type);
        },

        updateLoaderText: function(newText, duration = 500) {
            const loaderText = document.getElementById('loader-text');
            if (!loaderText) return;
            
            // Cancel any pending timeout
            if (this._loaderTextTimeoutId !== null) {
                clearTimeout(this._loaderTextTimeoutId);
                this._loaderTextTimeoutId = null;
            }
            
            // Start fade out
            loaderText.classList.add('changing');
            
            // Set new timeout and store its ID
            this._loaderTextTimeoutId = setTimeout(() => {
                loaderText.textContent = newText;
                loaderText.classList.remove('changing');
                this._loaderTextTimeoutId = null;
            }, duration);
        },

        showSkipWaitingLink: function(delay = 3000) {
            const skipWaiting = document.getElementById('skip-waiting');
            if (!skipWaiting) return;
            
            skipWaiting.addEventListener('click', (e) => {
                e.preventDefault();
                this.skipWaiting();
            });
    
            // Show the skip waiting link after the specified delay
            setTimeout(() => {
                skipWaiting.classList.add('visible');
            }, delay);
        },

        skipWaiting: function() {
            this._skipWaiting = true;
            // Handle initial route
            this.handleRoute();
            // Remove the loading screen
            this.hideLoader();
            // Listen for hash changes (guard against duplicate registration)
            if (!this._hashListenerAdded) {
                this._hashListenerAdded = true;
                window.addEventListener('hashchange', this.handleRoute);
            }
        },

        hideLoader: function() {
            // Hide the main page loader
            const loader = document.getElementById('fullpage-loader');
            if (loader) {
                loader.classList.add('hidden');
            }
        },
        
        showPage: function(pageId) {
            // Update URL hash without triggering the hashchange event
            const newHash = pageId.replace('-page', '');
            history.replaceState(null, '', `#/${newHash}`);

            // Hide all pages
            document.querySelectorAll('.page-container').forEach(container => {
                container.style.display = 'none';
            });
            
            // Show selected page
            const selectedPage = document.getElementById(pageId);
            if (selectedPage) {
                selectedPage.style.display = 'block';
            }
            
            // Update active state in navigation
            document.querySelectorAll('.nav-item').forEach(item => {
                item.classList.remove('active');
            });
            document.querySelector(`[data-page="${pageId}"]`).classList.add('active');
            
            // Update page title
            const pageTitle = document.querySelector('.page-title');
            switch(pageId) {
                case 'apps-page':
                    pageTitle.textContent = 'Applications';
                    break;
                case 'tunnels-page':
                    pageTitle.textContent = 'Tunnels (Open New Ports)';
                    break;
                case 'services-page':
                    pageTitle.textContent = 'Supervisor';
                    services.refresh();
                    break;
                case 'logs-page':
                    pageTitle.textContent = 'Instance Logs';
                    this.logManager.scrollToBottom();
                    break;
                case 'tools-page':
                pageTitle.textContent = 'Tools & Help';
                break;
            }

            // Close sidebar on mobile after navigation
            document.body.classList.remove('sidebar-open');
        },

        toggleSidebar: function() {
            document.body.classList.toggle('sidebar-open');
        },

        handleRoute: function() {
            let hash = window.location.hash.slice(2) || 'apps';
            // Redirect away from services page in VM mode (no supervisor)
            if (hash === 'services' && appUI.systemMetrics?.data?.environment !== 'container') {
                hash = 'apps';
            }
            appUI.showPage(`${hash}-page`);
        },

        redirectIfInsecure: async function() {
            const urlParams = new URLSearchParams(window.location.search);
            if (window.isSecureContext || urlParams.get('redir') === 'false') {
                // We're already secure or user wants IP so no need to wait for tunnel
                return false;
            } else {
                this.showSkipWaitingLink(1000);
                try {
                    // Instance Portal
                    app = this.applications.findByPort(1111)[0];
                    if (app.named_tunnel && app.named_tunnel_url) {
                        window.location.href = app.named_tunnel_url;
                    } else if (app.quick_tunnel_url && await this.tunnels.canResolve(app.quick_tunnel.tunnelUrl, 15000)) {
                        if (this._skipWaiting !== true) {
                            window.location.href = app.quick_tunnel_url;
                        }
                    }
                    await new Promise(resolve => setTimeout(resolve, 5000));
                }
                catch(e) {
                    return false;
                }
            }
        },
        
        // Modified init function to store references
        init: async function() {
            const parent = this;
            // Load apps & direct links with returned reference
            parent.updateLoaderText("Gathering applications...");
            this.applications = applications;
            await applications.init();
    
            // Load tunnels with returned reference
            parent.updateLoaderText("Loading secure tunnels...");
            this.tunnels = tunnels;
            await tunnels.init();
    
            // Load supervisor services
            this.services = services;
            services.init();

            // Begin fetching logs immediately with returned reference
            this.logManager = logManager;
            logManager.init();
            
            // Begin metrics collection with returned reference
            this.systemMetrics = systemMetrics;
            systemMetrics.init();
            
            // Redirect away from the IP address if not secure context
            await this.redirectIfInsecure();
            if (this._skipWaiting !== true) {
                // Handle initial route
                this.handleRoute();

                // Remove the loading screen
                this.hideLoader();

                // Listen for hash changes (guard against duplicate registration)
                if (!this._hashListenerAdded) {
                    this._hashListenerAdded = true;
                    window.addEventListener('hashchange', this.handleRoute);
                }
            }

            return this;
        }
    };
    
    return {
        init: function() {
            return appUI.init();
        },

        appUI: appUI
    };
})();
