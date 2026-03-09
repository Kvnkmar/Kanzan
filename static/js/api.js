/**
 * API client wrapper for the Kanzen Suite platform.
 * Handles CSRF tokens, session authentication, and JSON responses.
 */
const Api = {
  /**
   * Get the CSRF token from the cookie.
   */
  getCsrfToken() {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
  },

  /**
   * Make an API request.
   * @param {string} url - API endpoint URL
   * @param {object} options - fetch options
   * @returns {Promise<object>} - parsed JSON response
   */
  async request(url, options = {}) {
    const defaults = {
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': this.getCsrfToken(),
      },
      credentials: 'same-origin',
    };

    const config = {
      ...defaults,
      ...options,
      headers: { ...defaults.headers, ...(options.headers || {}) },
    };

    const response = await fetch(url, config);

    if (response.status === 204) return null;

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw { status: response.status, ...error };
    }

    return response.json();
  },

  get(url) {
    return this.request(url, { method: 'GET' });
  },

  post(url, data) {
    return this.request(url, { method: 'POST', body: JSON.stringify(data) });
  },

  patch(url, data) {
    return this.request(url, { method: 'PATCH', body: JSON.stringify(data) });
  },

  put(url, data) {
    return this.request(url, { method: 'PUT', body: JSON.stringify(data) });
  },

  delete(url) {
    return this.request(url, { method: 'DELETE' });
  },

  /**
   * Upload a file via multipart form data.
   */
  async upload(url, formData) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': this.getCsrfToken() },
      credentials: 'same-origin',
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw { status: response.status, ...error };
    }

    return response.json();
  },
};
