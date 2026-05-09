import brasaoSrc from '../assets/brasao-sorocaba.svg';

/**
 * Header component — identidade visual Sophia com brasão de Sorocaba.
 * Nome à esquerda, subtítulo centralizado, brasão no canto direito.
 *
 * Requirements: 1.1, 1.3
 */
export function Header(): JSX.Element {
  return (
    <header className="header-sophia" data-testid="sophia-header">
      <div className="header-sophia-content">
        <div className="header-left">
          <h1><span className="header-sophia-underline">Sophia</span></h1>
        </div>
        <div className="header-center">
          <p>Análise comparativa de arquiteturas multiagente <br /> Gastos em Saúde de Sorocaba-SP</p>
        </div>
        <div className="header-right">
          <img
            src={brasaoSrc}
            alt="Brasão de Sorocaba"
            className="header-brasao"
          />
        </div>
      </div>
    </header>
  );
}
