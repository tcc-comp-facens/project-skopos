import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { TabNav } from './TabNav';

describe('TabNav', () => {
  it('renders both tab buttons', () => {
    render(<TabNav activeTab="user" onTabChange={vi.fn()} />);
    expect(screen.getByTestId('tab-user')).toBeInTheDocument();
    expect(screen.getByTestId('tab-tech')).toBeInTheDocument();
  });

  it('sets aria-selected="true" on the active tab (user)', () => {
    render(<TabNav activeTab="user" onTabChange={vi.fn()} />);
    expect(screen.getByTestId('tab-user')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('tab-tech')).toHaveAttribute('aria-selected', 'false');
  });

  it('sets aria-selected="true" on the active tab (tech)', () => {
    render(<TabNav activeTab="tech" onTabChange={vi.fn()} />);
    expect(screen.getByTestId('tab-tech')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('tab-user')).toHaveAttribute('aria-selected', 'false');
  });

  it('calls onTabChange("tech") when Técnica tab is clicked', async () => {
    const onTabChange = vi.fn();
    const user = userEvent.setup();
    render(<TabNav activeTab="user" onTabChange={onTabChange} />);

    await user.click(screen.getByTestId('tab-tech'));
    expect(onTabChange).toHaveBeenCalledWith('tech');
  });

  it('calls onTabChange("user") when Usuário tab is clicked', async () => {
    const onTabChange = vi.fn();
    const user = userEvent.setup();
    render(<TabNav activeTab="tech" onTabChange={onTabChange} />);

    await user.click(screen.getByTestId('tab-user'));
    expect(onTabChange).toHaveBeenCalledWith('user');
  });

  it('has role="tablist" on the container', () => {
    render(<TabNav activeTab="user" onTabChange={vi.fn()} />);
    expect(screen.getByTestId('tab-nav')).toHaveAttribute('role', 'tablist');
  });

  it('has role="tab" on each button', () => {
    render(<TabNav activeTab="user" onTabChange={vi.fn()} />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(2);
  });
});
