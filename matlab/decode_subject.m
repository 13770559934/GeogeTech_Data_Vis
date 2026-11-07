function results = decode_subject(manifest_path, result_path)
% DECODE_SUBJECT  Decode MATLAB table .mat files into plain intermediate .mat files.
%
%   The Georgia Tech dataset stores every trial as a MATLAB `table`, which in a
%   v5 MAT-file is serialized as an MCOS opaque object.  Neither scipy.io.loadmat
%   nor pymatreader can read that; only MATLAB itself can.  This function is the
%   MATLAB half of convert_mat_to_csv.py: it loads each table and re-saves it as
%   a plain (numeric matrix + string cell) .mat that Python can read with
%   scipy.io.loadmat.
%
%   manifest_path : JSON file written by convert_mat_to_csv.py
%                   {"src": ["a.mat", ...], "dst": ["b.mat", ...]}
%                   src[i] and dst[i] are paired.
%   result_path   : JSON file to write.  Contains
%                   {"results": [{"src":..., "dst":..., "ok":true/false,
%                                 "error":..., "rows":N, "cols":M}, ...]}
%
%   Intermediate layout (saved with -v7 so scipy can read it):
%       cols     : 1xM cell of char  -- original column names, in order
%       num_data : NxM double       -- numeric columns; NaN where the column
%                                      is a string
%       str_data : NxK cell of char -- string columns (K = 0 if none)
%       is_str   : 1xM logical      -- true for string columns
%
%   A failure on one file never aborts the run: the error is captured and the
%   loop continues.

    if nargin < 1 || isempty(manifest_path)
        error('decode_subject:manifest', 'manifest_path is required');
    end
    if nargin < 2 || isempty(result_path)
        result_path = '';
    end

    manifest = jsondecode(fileread(manifest_path));
    srcs = manifest.src;
    dsts = manifest.dst;
    if ~iscell(srcs)
        srcs = {srcs};
    end
    if ~iscell(dsts)
        dsts = {dsts};
    end
    n = numel(srcs);
    if numel(dsts) ~= n
        error('decode_subject:manifest', 'src/dst length mismatch (%d vs %d)', ...
              n, numel(dsts));
    end

    results = repmat(struct('src', '', 'dst', '', 'ok', false, ...
                             'error', '', 'rows', 0, 'cols', 0), n, 1);

    for i = 1:n
        src = srcs{i};
        dst = dsts{i};
        results(i).src = src;
        results(i).dst = dst;
        try
            info = decode_one(src, dst);
            results(i).ok = true;
            results(i).rows = info.rows;
            results(i).cols = info.cols;
        catch err
            results(i).ok = false;
            results(i).error = err.message;
            fprintf(2, '[decode_subject] FAILED %s : %s\n', src, err.message);
        end
    end

    if ~isempty(result_path)
        out = struct('results', results);
        write_json(out, result_path);
    end
end


% ─────────────────────────────────────────────────────────────────────────────
function info = decode_one(src, dst)
% Decode a single .mat file containing one MATLAB table.

    S = load(src);

    % Locate the table variable: the first top-level variable that is a table.
    names = fieldnames(S);
    tname = '';
    for k = 1:numel(names)
        if istable(S.(names{k}))
            tname = names{k};
            break;
        end
    end
    if isempty(tname)
        error('decode_subject:notable', 'no table variable found');
    end
    T = S.(tname);

    cols = T.Properties.VariableNames;
    n = height(T);
    m = numel(cols);

    is_str = false(1, m);
    num_data = nan(n, m);
    str_data = cell(n, 0);
    str_names = {};

    for j = 1:m
        v = T.(cols{j});
        if isnumeric(v) || islogical(v)
            num_data(:, j) = double(v);
        else
            is_str(j) = true;
            if iscell(v)
                c = cellfun(@to_char_row, v, 'UniformOutput', false);
            else
                c = cellstr(string(v));
            end
            str_data(:, end + 1) = c(:);
            str_names{end + 1} = cols{j};
        end
    end

    % Make sure the destination directory exists.
    d = fileparts(dst);
    if ~isempty(d) && ~isfolder(d)
        mkdir(d);
    end

    save(dst, '-v7', 'cols', 'num_data', 'str_data', 'is_str');

    info = struct('rows', n, 'cols', m);
end


% ─────────────────────────────────────────────────────────────────────────────
function s = to_char_row(x)
% Normalize one cell element to a plain char row vector.

    if ischar(x)
        s = x;
    elseif isstring(x) && isscalar(x)
        s = char(x);
    elseif isnumeric(x) && isscalar(x)
        s = num2str(x);
    else
        s = '';
    end
    s = reshape(s, 1, []);
end


% ─────────────────────────────────────────────────────────────────────────────
function write_json(value, path)
% Minimal JSON writer (no external toolbox needed).

    fid = fopen(path, 'w');
    if fid < 0
        error('decode_subject:json', 'cannot open %s for writing', path);
    end
    cleaner = onCleanup(@() fclose(fid));
    write_value(fid, value);
    fprintf(fid, '\n');
end


function write_value(fid, v)
    if isstruct(v) && numel(v) == 1
        f = fieldnames(v);
        fprintf(fid, '{');
        for i = 1:numel(f)
            if i > 1
                fprintf(fid, ',');
            end
            write_string(fid, f{i});
            fprintf(fid, ':');
            write_value(fid, v.(f{i}));
        end
        fprintf(fid, '}');
    elseif isstruct(v)
        fprintf(fid, '[');
        for i = 1:numel(v)
            if i > 1
                fprintf(fid, ',');
            end
            write_value(fid, v(i));
        end
        fprintf(fid, ']');
    elseif islogical(v)
        if numel(v) == 1
            if v
                fprintf(fid, 'true');
            else
                fprintf(fid, 'false');
            end
        else
            fprintf(fid, '[');
            for i = 1:numel(v)
                if i > 1
                    fprintf(fid, ',');
                end
                write_value(fid, v(i));
            end
            fprintf(fid, ']');
        end
    elseif iscell(v)
        fprintf(fid, '[');
        for i = 1:numel(v)
            if i > 1
                fprintf(fid, ',');
            end
            write_value(fid, v{i});
        end
        fprintf(fid, ']');
    elseif ischar(v)
        write_string(fid, v);
    elseif isnumeric(v)
        if isscalar(v)
            if isnan(v)
                fprintf(fid, 'null');
            else
                fprintf(fid, '%.17g', v);
            end
        else
            fprintf(fid, '[');
            for i = 1:numel(v)
                if i > 1
                    fprintf(fid, ',');
                end
                write_value(fid, v(i));
            end
            fprintf(fid, ']');
        end
    else
        fprintf(fid, 'null');
    end
end


function write_string(fid, s)
    s = reshape(s, 1, []);
    s = strrep(s, '\', '\\');
    s = strrep(s, '"', '\"');
    s = strrep(s, sprintf('\n'), '\n');
    s = strrep(s, sprintf('\r'), '\r');
    s = strrep(s, sprintf('\t'), '\t');
    fprintf(fid, '"%s"', s);
end
