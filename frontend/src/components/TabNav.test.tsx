/**
 * Tests for TabNav component.
 * Validates tab switching and accessibility attributes.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TabNav } from './TabNav';

describe('TabNav', () => {
  it('renders both tab buttons', () => {
    render(<TabNav activeTab="user" onTabChange={vi.fn()} />);
    expect(screen.getByTestId('tab-user')).toBeInTheDocument();
    expect(screen.getByTestId('tab-tech')).toBeInTheDocument();
  });

  it('marks active tab with aria-selected=true', () => {
    render(<TabNav activeTab="user" onTabChange={vi.fn()} />);
    expect(screen.getByTestId('tab-user')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('tab-tech')).toHaveAttribute('aria-selected', 'false');
  });

  it('calls onTabChange with "tech" when tech tab clicked', () => {
    const onTabChange = vi.fn();
    render(<TabNav activeTab="user" onTabChange={onTabChange} />);
    fireEvent.click(screen.getByTestId('tab-tech'));
    expect(onTabChange).toHaveBeenCalledWith('tech');
  });

  it('calls onTabChange with "user" when user tab clicked', () => {
    const onTabChange = vi.fn();
    render(<TabNav activeTab="tech" onTabChange={onTabChange} />);
    fireEvent.click(screen.getByTestId('tab-user'));
    expect(onTabChange).toHaveBeenCalledWith('user');
  });

  it('has role="tablist" on nav element', () => {
    render(<TabNav activeTab="user" onTabChange={vi.fn()} />);
    expect(screen.getByTestId('tab-nav')).toHaveAttribute('role', 'tablist');
  });
});
