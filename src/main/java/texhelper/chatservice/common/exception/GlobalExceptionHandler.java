package texhelper.chatservice.common.exception;

import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import texhelper.chatservice.common.response.ApiResponse;

import java.sql.SQLIntegrityConstraintViolationException;
import java.util.HashMap;
import java.util.Map;

@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {
    // BaseException 예외 = 정의된 예외(BaseException)를 처리
    @ExceptionHandler(BaseException.class)
    public ResponseEntity<ApiResponse<?>> handleBaseException(BaseException ex) {
        log.error("BaseException: ", ex);
        return ResponseEntity.status(ex.getStatus())
                .body(ApiResponse.errorWithOutData(ex.getExceptionEnum(), ex.getStatus()));
    }

    // SQL 무결성 예외 처리
    @ExceptionHandler(SQLIntegrityConstraintViolationException.class)
    public ResponseEntity<ApiResponse<?>> handleSQLIntegrityConstraintViolationException(
            SQLIntegrityConstraintViolationException ex) {
        log.error("SQLIntegrityConstraintViolationException: ", ex);
        return ResponseEntity.status(ExceptionEnum.DATA_INTEGRITY_VIOLATION.getStatus())
                .body(ApiResponse.errorWithOutData(ExceptionEnum.DATA_INTEGRITY_VIOLATION,
                        ExceptionEnum.DATA_INTEGRITY_VIOLATION.getStatus()));
    }

    // 데이터 무결성 예외 처리
    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<ApiResponse<?>> handleDataIntegrityViolationException(
            DataIntegrityViolationException ex) {
        log.error("DataIntegrityViolationException: ", ex);
        return ResponseEntity.status(ExceptionEnum.DATA_INTEGRITY_VIOLATION.getStatus())
                .body(ApiResponse.errorWithOutData(ExceptionEnum.DATA_INTEGRITY_VIOLATION,
                        ExceptionEnum.DATA_INTEGRITY_VIOLATION.getStatus()));
    }

    // @Valid 검증 실패 시 발생하는 예외 처리
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiResponse<?>> handleMethodArgumentNotValidException(
            MethodArgumentNotValidException ex) {
        log.error("MethodArgumentNotValidException: ", ex);
        // 유효성 검사 실패 시 발생한 오류 메시지를 필드별로 저장
        Map<String, String> errors = new HashMap<>();
        for (FieldError error : ex.getBindingResult().getFieldErrors()) {
            errors.put(error.getField(), error.getDefaultMessage());
        }
        // 필드별 오류 메시지를 data로 전달 (예시 :"data": {"password": "비밀번호를 입력해주세요."}
        return ResponseEntity.status(ExceptionEnum.INVALID_INPUT_VALUE.getStatus())
                .body(ApiResponse.errorWithData(ExceptionEnum.INVALID_INPUT_VALUE,
                        ExceptionEnum.INVALID_INPUT_VALUE.getStatus(), errors));
    }

    // http 요청이 잘못된 경우 발생하는 예외 처리
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiResponse<?>> handleHttpMessageNotReadableException(
            HttpMessageNotReadableException ex) {
        log.error("HttpMessageNotReadableException: ", ex);
        return ResponseEntity.status(ExceptionEnum.INVALID_INPUT_VALUE.getStatus())
                .body(ApiResponse.errorWithOutData(ExceptionEnum.INVALID_INPUT_VALUE,
                        ExceptionEnum.INVALID_INPUT_VALUE.getStatus()));
    }

    // 그 외 예상치 못한 예외 처리
    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResponse<?>> handleOtherExceptions(Exception ex) {
        log.error("Unexpected exception: ", ex);
        return ResponseEntity.status(ExceptionEnum.INTERNAL_SERVER_ERROR.getStatus())
                .body(ApiResponse.errorWithOutData(ExceptionEnum.INTERNAL_SERVER_ERROR,
                        ExceptionEnum.INTERNAL_SERVER_ERROR.getStatus()));
    }
}
