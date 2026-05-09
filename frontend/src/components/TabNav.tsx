/**
 * TabNav — barra de navegação entre as abas Usuário e Técnica.
 *
 * Requirements: 2.1, 2.2, 2.3, 2.5
 */
export interface TabNavProps {
  activeTab: 'user' | 'tech';
  onTabChange: (tab: 'user' | 'tech') => void;
}

export function TabNav({ activeTab, onTabChange }: TabNavProps): JSX.Element {
  return (
    <nav
      role="tablist"
      className="tab-nav"
      data-testid="tab-nav"
      aria-label="Navegação entre abas"
    >
      <button
        role="tab"
        className="tab-btn"
        aria-selected={activeTab === 'user'}
        aria-controls="panel-user"
        data-testid="tab-user"
        onClick={() => onTabChange('user')}
      >
        Saúde
      </button>
      <button
        role="tab"
        className="tab-btn"
        aria-selected={activeTab === 'tech'}
        aria-controls="panel-tech"
        data-testid="tab-tech"
        onClick={() => onTabChange('tech')}
      >
        Agentes
      </button>
    </nav>
  );
}
