const TOOL_ENDPOINTS = {
  readFile: '/api/v1/tools/readFile',
  listFiles: '/api/v1/tools/listFiles',
  readSpecPages: '/api/v1/tools/readSpecPages',
  readSpecSection: '/api/v1/tools/readSpecSection',
  searchSpec: '/api/v1/tools/searchSpec',
  createFile: '/api/v1/tools/createFile',
  applyCodeToFile: '/api/v1/tools/applyCodeToFile',
  runSimulation: '/api/v1/tools/runSimulation',
};

export class ToolExecutor {
  constructor(baseUrl = 'http://localhost:7348', authToken = null) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.authToken = authToken;
  }

  setAuthToken(token) {
    this.authToken = token;
  }

  async _request(endpoint, body = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (this.authToken) {
      headers['Authorization'] = `Bearer ${this.authToken}`;
    }

    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        const text = await response.text().catch(() => null);
        return { success: false, data: null, error: text || `HTTP ${response.status}` };
      }

      const data = await response.json();
      return { success: true, data, error: null };
    } catch (err) {
      return { success: false, data: null, error: err.message || 'Network error' };
    }
  }

  async execute(toolName, args = {}) {
    const endpoint = TOOL_ENDPOINTS[toolName];
    if (!endpoint) {
      return { success: false, data: null, error: `Unknown tool: ${toolName}` };
    }
    return this._request(endpoint, args);
  }

  async readFile(artifactId) {
    return this.execute('readFile', { artifact_id: artifactId });
  }

  async listFiles(projectId) {
    return this.execute('listFiles', { project_id: projectId });
  }

  async readSpecPages(projectId, startPage, endPage, artifactId = undefined) {
    const body = {
      project_id: projectId,
      start_page: startPage,
      end_page: endPage,
    };
    if (artifactId) {
      body.artifact_id = artifactId;
    }
    return this.execute('readSpecPages', body);
  }

  async readSpecSection(projectId, sectionTitle, artifactId = undefined) {
    const body = {
      project_id: projectId,
      section_title: sectionTitle,
    };
    if (artifactId) {
      body.artifact_id = artifactId;
    }
    return this.execute('readSpecSection', body);
  }

  async searchSpec(projectId, query, limit = 12, artifactId = undefined) {
    const body = {
      project_id: projectId,
      query,
      limit,
    };
    if (artifactId) {
      body.artifact_id = artifactId;
    }
    return this.execute('searchSpec', body);
  }

  async createFile(projectId, filename, artifactType, content) {
    return this.execute('createFile', {
      project_id: projectId,
      filename,
      artifact_type: artifactType,
      content,
    });
  }

  async applyCodeToFile(artifactId, code, strategy, oldContent) {
    const body = { artifact_id: artifactId, code, strategy };
    if (oldContent !== undefined) {
      body.old_content = oldContent;
    }
    return this.execute('applyCodeToFile', body);
  }

  async runSimulation(projectId) {
    return this.execute('runSimulation', { project_id: projectId });
  }
}



