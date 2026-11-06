/**
 * Tests for adding and editing printers from a remote Bambuddy server.
 */

import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { AddPrinterModal } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('AddPrinterModal Remote Bambuddy Server mode', () => {
  it('discovers printers from a remote Bambuddy server and submits remote printer config', async () => {
    let discoverPayload: Record<string, unknown> | null = null;
    server.use(
      http.get('/api/v1/discovery/info', () => {
        return HttpResponse.json({
          is_docker: false,
          ssdp_running: false,
          scan_running: false,
          subnets: ['192.168.1.0/24'],
        });
      }),
      http.post('/api/v1/printers/remote/discover', async ({ request }) => {
        discoverPayload = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json([
          {
            remote_printer_id: 42,
            name: 'Lab B X1C',
            serial_number: '00M09A350199999',
            ip_address: '10.20.30.40',
            model: 'X1C',
            location: 'Lab B',
            is_active: true,
          },
        ]);
      })
    );

    const onAdd = vi.fn();
    const onClose = vi.fn();

    render(
      <AddPrinterModal
        onClose={onClose}
        onAdd={onAdd}
        existingSerials={[]}
      />
    );

    // Switch to Remote Bambuddy Server mode
    const remoteModeBtn = screen.getByRole('button', { name: /remote bambuddy server/i });
    await userEvent.click(remoteModeBtn);

    // Fill in Remote Server URL and API Key
    const urlInput = screen.getByPlaceholderText('http://192.168.2.50:8000');
    await userEvent.type(urlInput, 'http://192.168.2.50:8000');

    const apiKeyInput = screen.getByPlaceholderText('bb_...');
    await userEvent.type(apiKeyInput, 'bb_test_key_123');

    // Click Fetch Printers from Server
    const fetchBtn = screen.getByRole('button', { name: /fetch printers from server/i });
    await userEvent.click(fetchBtn);

    await waitFor(() => {
      expect(screen.getByText(/Lab B X1C \(#42\)/)).toBeInTheDocument();
    });
    expect(discoverPayload).toEqual({
      server_url: 'http://192.168.2.50:8000',
      auth_type: 'api_key',
      api_key: 'bb_test_key_123',
    });

    // Click the discovered remote printer to populate the form
    await userEvent.click(screen.getByText(/Lab B X1C \(#42\)/));

    expect(screen.getByDisplayValue('Lab B X1C')).toBeInTheDocument();
    expect(screen.getByDisplayValue('00M09A350199999')).toBeInTheDocument();
    expect(screen.getByDisplayValue('10.20.30.40')).toBeInTheDocument();
    expect(screen.getByDisplayValue('42')).toBeInTheDocument();

    // Submit the form
    const submitBtn = screen.getByRole('button', { name: /^add printer$/i });
    await userEvent.click(submitBtn);

    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Lab B X1C',
        serial_number: '00M09A350199999',
        ip_address: '10.20.30.40',
        is_remote: true,
        remote_server_url: 'http://192.168.2.50:8000',
        remote_printer_id: 42,
        remote_auth_type: 'api_key',
        remote_api_key: 'bb_test_key_123',
      })
    );
  });
});
