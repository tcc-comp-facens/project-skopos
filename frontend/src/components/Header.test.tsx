/**
 * Tests for Header component.
 * Validates identity elements: name, subtitle, brasão.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Header } from './Header';

describe('Header', () => {
  it('renders Sophia title', () => {
    render(<Header />);
    expect(screen.getByText('Sophia')).toBeInTheDocument();
  });

  it('renders subtitle with Sorocaba reference', () => {
    render(<Header />);
    expect(screen.getByText(/Sorocaba/)).toBeInTheDocument();
  });

  it('renders brasão image with alt text', () => {
    render(<Header />);
    const img = screen.getByAltText('Brasão de Sorocaba');
    expect(img).toBeInTheDocument();
  });

  it('has data-testid sophia-header', () => {
    render(<Header />);
    expect(screen.getByTestId('sophia-header')).toBeInTheDocument();
  });
});
